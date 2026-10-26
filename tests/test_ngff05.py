"""V3 metadata, retained labels, materialization and rollback contracts."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from test_result_zarr import (
    _identity, _input, _make_image, _make_plate, _manifest, _plate_input,
    IdentityProvider, NodeIdentityProvider,
)
from test_failures import snapshot
from biomero_shallower.operations import normalize, validate_report
from biomero_shallower.result_zarr import (
    evaluate_returned_zarr, materialize_shallow_zarr,
    normalize_returned_zarr, resolve_shallow_registration,
)
from biomero_shallower.transaction import recover


PROFILE = "ngff-0.5-zarr-v3"


def v3_metadata(root):
    # Existing fixtures deliberately use opaque pixel bytes and a stub identity
    # provider. These tests exercise filesystem transactions, not decoding.
    for path in list(root.rglob(".zattrs")):
        attrs = json.loads(path.read_text())
        for ms in attrs.get("multiscales", []):
            ms.pop("version", None)
        doc = {"zarr_format": 3, "node_type": "group",
               "attributes": {"ome": {"version": "0.5", **attrs},
                              "untouched": {"sample": "metadata"}},
               "consolidated_metadata": None}
        (path.parent / "zarr.json").write_text(json.dumps(doc))
        path.unlink()
        group = path.parent / ".zgroup"
        if group.exists():
            group.unlink()
    for path in list(root.rglob(".zarray")):
        doc = json.loads(path.read_text())
        doc.update(zarr_format=3, node_type="array", data_type="uint16")
        doc.pop("dtype")
        (path.parent / "zarr.json").write_text(json.dumps(doc))
        path.unlink()


def manifest(plate=False):
    item = _plate_input() if plate else _input(0)
    raw = item.to_dict()

    def replace(value):
        if isinstance(value, dict):
            for key in value:
                if key == "interchangeProfile":
                    value[key] = PROFILE
                else:
                    replace(value[key])
        elif isinstance(value, list):
            for child in value:
                replace(child)
    replace(raw)
    return _manifest(type(item).from_dict(raw))


def provider(plate=False):
    if not plate:
        return IdentityProvider(_identity())
    return NodeIdentityProvider({
        path: _identity(code, path, role)
        for path, code, role in [
            ("A/1/0", "ISCC:IA", "image"), ("B/1/0", "ISCC:IB", "image"),
            ("A/1/0/labels/cells", "ISCC:ILA", "label"),
            ("B/1/0/labels/cells", "ISCC:ILB", "label"),
        ]
    })


def make(root, plate=False):
    if plate:
        _make_plate(root, {"A/1/0": ("cells",), "B/1/0": ("cells",)})
    else:
        _make_image(root, labels=("cells",))
    v3_metadata(root)


@pytest.mark.parametrize("plate", [False, True])
def test_v3_normalize_and_materialize(tmp_path, plate):
    inputs = manifest(plate)
    canonical = inputs.inputs[0].plate_source or inputs.inputs[0].source
    source = tmp_path / canonical.relative_path
    make(source, plate)
    before_source = snapshot(source)
    root = tmp_path / "result.zarr"
    make(root, plate)
    before = snapshot(root)
    report = normalize(root, inputs, identity_provider=provider(plate))
    assert report.result == "normalized"
    assert report.adapter == PROFILE
    assert report.manifest.interchange_profile == PROFILE
    assert normalize(root, inputs, identity_provider=object()) == report
    assert validate_report(root, inputs) == report
    paths = ("A/1/0", "B/1/0") if plate else (".",)
    for path in paths:
        node = root / path
        assert not (node / "0").exists()
        assert (node / "labels/cells/0").is_dir()
        doc = json.loads((node / "zarr.json").read_text())
        assert "multiscales" not in doc["attributes"]["ome"]
        assert doc["attributes"]["untouched"] == {"sample": "metadata"}
    roots = {"group-0-data": tmp_path, "import-mount-data": tmp_path}
    registration = resolve_shallow_registration(
        root, storage_roots=roots, import_mount_path=tmp_path)
    destination = tmp_path / "next-workflow.zarr"
    materialize_shallow_zarr(registration.reference, destination, roots)
    for path in paths:
        for suffix in ("0/0.0.0.0", "labels/cells/0/0.0.0.0"):
            key = (Path(path) / suffix).as_posix()
            assert (destination / key).read_bytes() == before[key]
        attrs = json.loads((destination / path / "labels/zarr.json").read_text())
        assert attrs["attributes"]["ome"]["labels"] == ["cells"]
    assert not list(destination.rglob(".zattrs"))
    assert snapshot(source) == before_source


@pytest.mark.parametrize("plate", [False, True])
def test_v3_precommit_failure_restores_metadata_and_pixels(tmp_path, plate):
    root = tmp_path / "result.zarr"
    make(root, plate)
    before = snapshot(root)
    with patch("biomero_shallower.operations.validate_report", side_effect=ValueError("injected")) as check:
        report = normalize(root, manifest(plate), identity_provider=provider(plate))
    check.assert_called_once()
    assert report.result == "kept-full"
    after = snapshot(root)
    after.pop(".biomero-shallow-report.json")
    assert after == before


def test_v3_interrupted_move_recovers_exact_original(tmp_path):
    root = tmp_path / "result.zarr"
    make(root)
    before = snapshot(root)
    inputs = manifest()
    decision = evaluate_returned_zarr(root, inputs, identity_provider=provider())

    def interrupt(source, target):
        Path(source).rename(target)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        normalize_returned_zarr(decision, inputs.workflow_id, replace=interrupt)
    recover(root)
    assert snapshot(root) == before


@pytest.mark.parametrize("unsupported", [
    "consolidated", "0.6", "v2-source", "changed-pixels", "mixed-label", "ambiguous",
])
def test_v3_unsupported_keeps_pixels(tmp_path, unsupported):
    root = tmp_path / "result.zarr"
    make(root)
    path = root / "zarr.json"
    doc = json.loads(path.read_text())
    inputs = manifest()
    if unsupported == "consolidated":
        doc["consolidated_metadata"] = {"kind": "inline", "metadata": {}}
    elif unsupported == "0.6":
        doc["attributes"]["ome"]["version"] = "0.6"
    elif unsupported == "v2-source":
        inputs = _manifest(_input(0))
    elif unsupported == "mixed-label":
        _make_image(root, "labels/cells")
        (root / "labels/cells/zarr.json").unlink()
    elif unsupported == "ambiguous":
        (root / ".zattrs").write_text("{}")
    path.write_text(json.dumps(doc))
    before = snapshot(root)
    identity_provider = (IdentityProvider(_identity("ISCC:ICHANGED"))
                         if unsupported == "changed-pixels" else provider())
    report = normalize(root, inputs, identity_provider=identity_provider)
    assert report.result == "kept-full"
    after = snapshot(root)
    after.pop(".biomero-shallow-report.json")
    assert after == before


def test_v3_legacy_guard_remains_v2_only(tmp_path):
    from biomero_shallower.pixel_identity import (
        PixelIdentityError, read_zarr_semantic_guard, read_zarr_v2_semantic_guard,
    )
    root = tmp_path / "result.zarr"
    make(root)
    guard = read_zarr_semantic_guard(root, ".")
    assert (guard.ngff_version, guard.zarr_format, guard.dtype) == ("0.5", 3, "uint16")
    with pytest.raises(PixelIdentityError, match="v2"):
        read_zarr_v2_semantic_guard(root, ".")


def test_v3_inherited_label_removed_and_reconstructed(tmp_path):
    from biomero_schema.zarr import ManagedZarrNode, ZarrLabelComponent
    inputs = manifest()
    item = inputs.inputs[0]
    label = ZarrLabelComponent(
        logical_node_path="labels/cells",
        pixel_identity=_identity(node_path="labels/cells", role="label"),
        source=ManagedZarrNode(
            storage_root=item.source.storage_root,
            relative_path=item.source.relative_path, node_path="labels/cells"),
    )
    inputs = _manifest(item.model_copy(update={"labels": (label,)}))
    canonical = tmp_path / item.source.relative_path
    make(canonical)
    before = snapshot(canonical)
    root = tmp_path / "result.zarr"
    make(root)
    report = normalize(root, inputs, identity_provider=provider())
    assert report.result == "normalized"
    assert not (root / "labels/cells").exists()
    roots = {"group-0-data": tmp_path, "import-mount-data": tmp_path}
    view = resolve_shallow_registration(root, storage_roots=roots, import_mount_path=tmp_path)
    dest = tmp_path / "reconstructed.zarr"
    materialize_shallow_zarr(view.reference, dest, roots)
    assert (dest / "labels/cells/0/0.0.0.0").read_bytes() == before["labels/cells/0/0.0.0.0"]
    assert snapshot(canonical) == before
