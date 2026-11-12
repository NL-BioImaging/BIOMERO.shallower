# -*- coding: utf-8 -*-
# Copyright 2026 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import logging
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from test_result_zarr import _make_image, _manifest, _input, _identity, IdentityProvider
from biomero_shallower.adapters import Ngff04ZarrV2
from biomero_shallower.operations import FORMAT_ADAPTERS, normalize
from biomero_shallower.evaluation import evaluate_returned_zarr
from biomero_shallower.pixel_identity import PixelIdentityError
from biomero_shallower.transaction import recover


def test_decision_and_reason_are_logged(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    _make_image(root)
    with caplog.at_level(logging.DEBUG, logger='biomero_shallower'):
        evaluate_returned_zarr(root, None)
    assert 'outcome=keep-full, reason=no-canonical-input-snapshot' in caplog.text


def test_normalize_logs_prune_commit_and_cleanup(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    with caplog.at_level(logging.INFO, logger='biomero_shallower'):
        normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity()))
    for expected in ('Pruning', 'Committed prune', 'Removing rollback folder',
                     'result=normalized'):
        assert expected in caplog.text
    assert str(root) in caplog.text


def test_recovery_is_logged(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    root.mkdir()
    rollback = tmp_path / '.result.zarr.biomero-prune-test'
    rollback.mkdir()
    (rollback / 'journal.json').write_text(json.dumps(
        {'schema': 1, 'committed': True, 'moves': [], 'attrs': {}}))
    with caplog.at_level(logging.INFO, logger='biomero_shallower'):
        recover(root)
    assert 'Recovering' in caplog.text and 'committed=True' in caplog.text


class _FailingProvider(IdentityProvider):
    """Identity provider that fails for the given node roles."""

    def __init__(self, identity, failing_roles):
        super().__init__(identity)
        self.failing_roles = failing_roles

    def generate(self, root, **kwargs):
        if kwargs['role'] in self.failing_roles:
            raise PixelIdentityError('injected')
        return super().generate(root, **kwargs)


def _unsupported_ngff(root, tmp_path):
    (root / 'zarr.json').write_text('{}')  # v2 and v3 metadata together are ambiguous
    return IdentityProvider(_identity())


def _identity_unavailable(root, tmp_path):
    return _FailingProvider(_identity(), {'image'})


def _label_identity_unavailable(root, tmp_path):
    return _FailingProvider(_identity(), {'label'})


def _pixels_changed(root, tmp_path):
    return IdentityProvider(_identity('ISCC:ICHANGED'))


@pytest.mark.parametrize('prepare, reason, warned', [
    (_unsupported_ngff, 'unsupported-ngff', True),
    (_identity_unavailable, 'identity-unavailable', True),
    (_label_identity_unavailable, 'label-identity-unavailable', True),
    (_pixels_changed, 'pixels-changed', False),
])
def test_real_evaluation_logs_failure_reasons_as_warnings(tmp_path, caplog, prepare, reason, warned):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    provider = prepare(root, tmp_path)
    with caplog.at_level(logging.DEBUG, logger='biomero_shallower'):
        decision = evaluate_returned_zarr(root, _manifest(_input(0, 'result.zarr')),
                                          identity_provider=provider)
    assert decision.reason.startswith(reason)
    assert any(r.levelno == logging.WARNING for r in caplog.records) is warned


def test_normalization_failure_is_warned_once_with_traceback(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    _make_image(root)
    with patch('biomero_shallower.operations.validate_report', side_effect=ValueError('injected')):
        with caplog.at_level(logging.INFO, logger='biomero_shallower'):
            normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity()))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any('failed: injected' in r.getMessage() and r.exc_info for r in warnings)
    assert not any(r.name.endswith("normalization") for r in warnings)


def test_profile_mismatch_is_warned(tmp_path, caplog):
    class OtherProfile(Ngff04ZarrV2):
        profile = 'ngff-0.5-zarr-v3'

    root = tmp_path / 'result.zarr'
    _make_image(root)
    with patch.dict(FORMAT_ADAPTERS, {2: OtherProfile()}):
        with caplog.at_level(logging.INFO, logger='biomero_shallower'):
            report = normalize(root, _manifest(_input(0)),
                               identity_provider=IdentityProvider(_identity()))
    assert report.reason == 'unsupported-canonical-profile'
    assert any(r.levelno == logging.WARNING and 'ngff-0.5-zarr-v3' in r.getMessage()
               for r in caplog.records)


def test_cli_stdout_is_one_json_document_with_logging(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root)
    manifest = tmp_path / 'canonical.json'
    manifest.write_text(json.dumps(_manifest().to_dict()), encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-m', 'biomero_shallower.cli', 'normalize',
         '--returned-zarr', str(root), '--canonical-inputs', str(manifest),
         '--contract-version', '1', '--report', str(tmp_path / 'report.json')],
        capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['result'] == 'kept-full'
    assert 'INFO' in result.stderr
