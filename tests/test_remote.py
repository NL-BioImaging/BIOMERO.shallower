import json
from pathlib import Path

import pytest

from test_result_zarr import _make_image, _input, _manifest, _identity, IdentityProvider
from biomero_shallower.operations import normalize, validate_report
from biomero_shallower.result_zarr import evaluate_returned_zarr, normalize_returned_zarr


def test_normalize_report_and_rerun_without_hashing(tmp_path):
    root = tmp_path / "result.zarr"
    _make_image(root, labels=("cells",))
    manifest = _manifest(_input(0))
    provider = IdentityProvider(_identity())
    report = normalize(root, manifest, identity_provider=provider)
    assert report.result == "normalized"
    calls = len(provider.calls)
    assert normalize(root, manifest, identity_provider=provider) == report
    assert len(provider.calls) == calls
    assert validate_report(root, manifest) == report


def test_interrupted_move_rolls_back_on_resume(tmp_path):
    root = tmp_path / "result.zarr"
    _make_image(root, labels=("cells",))
    manifest = _manifest(_input(0))
    decision = evaluate_returned_zarr(root, manifest, identity_provider=IdentityProvider(_identity()))
    def interrupt(source, target):
        Path(source).rename(target)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        normalize_returned_zarr(decision, manifest.workflow_id, replace=interrupt)
    assert not (root / "0").exists()
    report = normalize(root, manifest, identity_provider=IdentityProvider(_identity()))
    assert report.result == "normalized"
    assert not list(tmp_path.glob(".*.biomero-prune-*"))


def test_non_zarr_is_kept_and_unknown_contract_rejected(tmp_path):
    path = tmp_path / "result.csv"
    path.write_text("a,b")
    manifest = _manifest(_input(0))
    assert normalize(path, manifest).result == "kept-full"
    with pytest.raises(ValueError, match="contract"):
        normalize(path, manifest, contract=99)
    assert path.read_text() == "a,b"
