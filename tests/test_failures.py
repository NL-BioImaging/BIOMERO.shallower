import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_result_zarr import _make_image, _manifest, _input, _identity, IdentityProvider
from biomero_shallower.cli import load_manifest
from biomero_shallower.operations import normalize
from biomero_shallower.transaction import recover


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
    with patch('biomero_shallower.result_zarr.shutil.rmtree', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            normalize(root, manifest, identity_provider=IdentityProvider(_identity()))
    assert not (root / '0').exists()
    report = normalize(root, manifest, identity_provider=object())
    assert report.result == 'normalized'
    assert not list(tmp_path.glob('.*.biomero-prune-*'))


def test_production_does_not_measure_recursive_bytes(tmp_path):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    with patch('biomero_shallower.result_zarr._tree_size', side_effect=AssertionError('byte scan')):
        assert normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity())).result == 'normalized'
