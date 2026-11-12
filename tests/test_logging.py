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
from biomero_shallower.result_zarr import _keep_full, evaluate_returned_zarr
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


@pytest.mark.parametrize('reason, warned', [
    ('unsupported-ngff: mixed', True),
    ('unsupported-canonical-profile', True),
    ('identity-unavailable: boom', True),
    ('label-identity-unavailable: boom', True),
    ('no-canonical-input-snapshot', True),
    ('ambiguous-input-identity', True),
    ('pixels-changed', False),
    ('no-input-identity-match', False),
])
def test_keep_full_reason_log_level(tmp_path, caplog, reason, warned):
    with caplog.at_level(logging.DEBUG, logger='biomero_shallower'):
        _keep_full(tmp_path, reason)
    assert any(r.levelno == logging.WARNING for r in caplog.records) is warned


def test_normalization_failure_is_warned_once_with_traceback(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    _make_image(root)
    with patch('biomero_shallower.operations.validate_report', side_effect=ValueError('injected')):
        with caplog.at_level(logging.INFO, logger='biomero_shallower'):
            normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity()))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any('failed: injected' in r.getMessage() and r.exc_info for r in warnings)
    assert not any(r.name.endswith('result_zarr') for r in warnings)


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
