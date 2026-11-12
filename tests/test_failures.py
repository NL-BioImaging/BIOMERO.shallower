import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from test_result_zarr import _make_image, _manifest, _input, _identity, IdentityProvider
from biomero_shallower.cli import load_manifest
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST
from biomero_shallower.operations import normalize
from biomero_shallower.result_zarr import is_failure_reason
from biomero_shallower.transaction import (
    pending_prune_journals, prune_was_committed, recover, remove_rollback,
)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('schema', [None, 2, '1'])
def test_missing_and_incompatible_manifest_schema(tmp_path, schema):
    raw = _manifest(_input(0)).to_dict()
    if schema is None:
        raw.pop('schema')
    else:
        raw['schema'] = schema
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='contract version'):
        load_manifest(path)


def test_precommit_failure_restores_pixels_and_metadata(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    before = snapshot(root)
    with patch('biomero_shallower.operations.validate_report', side_effect=ValueError('injected')):
        report = normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity()))
    assert report.result == 'kept-full'
    after = snapshot(root)
    after.pop('.biomero-shallow-report.json')
    assert after == before
    assert not list(tmp_path.glob('.*.biomero-prune-*'))


def test_commit_cleanup_interruption_preserves_receipt(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    manifest = _manifest(_input(0))
    with patch('biomero_shallower.transaction.shutil.rmtree', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            normalize(root, manifest, identity_provider=IdentityProvider(_identity()))
    assert not (root / '0').exists()
    report = normalize(root, manifest, identity_provider=object())
    assert report.result == 'normalized'
    assert not list(tmp_path.glob('.*.biomero-prune-*'))


def test_interrupted_cleanup_is_recovered_automatically(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity()))
    rollback = tmp_path / '.result.zarr.biomero-prune-test'
    rollback.mkdir()
    (rollback / 'journal.json').write_text(json.dumps(
        {'schema': 1, 'committed': True, 'moves': [], 'attrs': {}}))
    (rollback / 'a').mkdir()
    (rollback / 'a' / 'chunk').write_bytes(b'x')
    (rollback / 'b').mkdir()
    real_rmtree = shutil.rmtree
    calls = []

    def interrupt_after_first(path, *args, **kwargs):
        if calls:
            raise KeyboardInterrupt
        calls.append(path)
        real_rmtree(path, *args, **kwargs)

    with patch('biomero_shallower.transaction.shutil.rmtree', side_effect=interrupt_after_first):
        with pytest.raises(KeyboardInterrupt):
            remove_rollback(rollback)
    assert (rollback / 'journal.json').exists()
    recover(root)
    assert not rollback.exists()


def _committed_rollback(tmp_path):
    root = tmp_path / 'result.zarr'
    root.mkdir()
    rollback = tmp_path / '.result.zarr.biomero-prune-test'
    (rollback / '0').mkdir(parents=True)
    (rollback / '0' / 'chunk').write_bytes(b'x')
    return root, rollback


@pytest.mark.parametrize('interrupted', ['unlink', 'rmdir'])
def test_cleanup_interrupted_at_journal_or_folder_converges(tmp_path, interrupted):
    root, rollback = _committed_rollback(tmp_path)
    (rollback / 'journal.json').write_text(json.dumps(
        {'schema': 1, 'committed': True, 'moves': ['0'], 'attrs': {}}))
    real = getattr(Path, interrupted)

    def interrupt(self, *args, **kwargs):
        if self == rollback / 'journal.json' or self == rollback:
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    with patch.object(Path, interrupted, interrupt):
        with pytest.raises(KeyboardInterrupt):
            remove_rollback(rollback)
    recover(root)
    recover(root)
    assert not rollback.exists()


def test_interrupted_uncommitted_recovery_restores_arrays_on_rerun(tmp_path):
    root, rollback = _committed_rollback(tmp_path)
    (rollback / 'journal.json').write_text(json.dumps(
        {'schema': 1, 'committed': False, 'moves': ['0'], 'attrs': {}}))
    with patch('biomero_shallower.transaction._delete_rollback_folder', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            recover(root)
    assert (root / '0' / 'chunk').read_bytes() == b'x'
    recover(root)
    assert (root / '0' / 'chunk').read_bytes() == b'x'
    assert not rollback.exists()


@pytest.mark.parametrize('journal_committed, manifest, expected', [
    (False, True, False),
    (True, True, True),
    (None, True, True),
    (None, False, False),
])
def test_prune_was_committed_from_journal_and_manifest(tmp_path, journal_committed, manifest, expected):
    root = tmp_path / 'result.zarr'
    root.mkdir()
    if manifest:
        (root / SHALLOW_COLLECTION_MANIFEST).write_text('{}')
    if journal_committed is not None:
        rollback = tmp_path / '.result.zarr.biomero-prune-test'
        rollback.mkdir()
        (rollback / 'journal.json').write_text(json.dumps(
            {'schema': 1, 'committed': journal_committed, 'moves': [], 'attrs': {}}))
        assert [journal.committed for journal in pending_prune_journals(root)] == [journal_committed]
    else:
        assert pending_prune_journals(root) == []
    assert prune_was_committed(root) is expected


@pytest.mark.parametrize('reason, failure', [
    ('no-canonical-input-snapshot', True),
    ('invalid-transfer-input-marker', True),
    ('untrusted-transfer-input-marker', True),
    ('ambiguous-transfer-artifact', True),
    ('ambiguous-input-identity', True),
    ('unsupported-canonical-profile', True),
    ('unsupported-ngff: mixed', True),
    ('identity-unavailable: boom', True),
    ('label-identity-unavailable: boom', True),
    ('normalization-failed: boom', True),
    ('no-image-nodes', False),
    ('image-layout-mismatch', False),
    ('image-semantics-mismatch', False),
    ('pixels-changed', False),
    ('no-input-identity-match', False),
    ('input-image-unchanged', False),
])
def test_is_failure_reason(reason, failure):
    assert is_failure_reason(reason) is failure


def test_production_does_not_measure_recursive_bytes(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    with patch('biomero_shallower.normalization._tree_size', side_effect=AssertionError('byte scan')):
        assert normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity())).result == 'normalized'
