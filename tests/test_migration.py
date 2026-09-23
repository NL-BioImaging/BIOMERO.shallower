import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT
from biomero_schema.zarr import (
    SHALLOW_COLLECTION_MANIFEST,
    ShallowManifest,
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrLabelComponent,
)
from biomero_shallower.cli import main
from biomero_shallower import __version__
from biomero_shallower.migration import (
    migrate_shallow_store_v1,
    restore_shallow_store_v1,
    upgrade_annotation_reference_v1,
    upgrade_manifest_v1,
)
from biomero_shallower.operations import validate_report

from test_result_zarr import _identity, _input, _make_image, _manifest


def _legacy_store(tmp_path):
    root = tmp_path / "result.zarr"
    _make_image(root, labels=("cells",))
    shutil.rmtree(root / "0")
    attrs_path = root / ".zattrs"
    attrs = json.loads(attrs_path.read_text(encoding="utf-8"))
    attrs.pop("multiscales")
    attrs["biomero"] = {
        "model": "rfc8-shallow-copy",
        "manifest": SHALLOW_COLLECTION_MANIFEST,
        "workflowId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    }
    attrs_path.write_text(json.dumps(attrs), encoding="utf-8")

    source_input = _input(0, "result.zarr")
    label = ZarrLabelComponent(
        logicalNodePath="labels/cells",
        pixelIdentity=_identity("ISCC:ILABEL", "labels/cells", "label"),
    )
    legacy = {
        "schema": 1,
        "model": "rfc8-shallow-copy",
        "workflowId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "transferArtifact": root.name,
        "interchangeProfile": "ngff-0.4-zarr-v2",
        "images": [{
            "imageNodePath": ".",
            "source": source_input.source.to_dict(),
            "returnedPixelIdentity": _identity().to_dict(),
            "labelNodePaths": ["labels/cells"],
            "labelComponents": [label.to_dict()],
        }],
    }
    manifest_path = root / SHALLOW_COLLECTION_MANIFEST
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    canonical = _manifest(source_input)
    report = {
        "schema": 1,
        "toolVersion": "0.1.0b1",
        "image": None,
        "inputContract": 1,
        "outputContract": 1,
        "adapter": "ngff-0.4-zarr-v2",
        "canonicalInputs": canonical.to_dict(),
        "artifact": root.name,
        "decision": "eligible",
        "reason": "input-image-unchanged",
        "result": "normalized",
        "collection": legacy,
        "timings": {"identity": 1.0, "normalization": 0.1},
        "slurmJobId": None,
        "taskId": None,
        "bytesBefore": None,
        "bytesAfter": None,
    }
    report_path = root / SHALLOW_OPERATION_REPORT
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return root, canonical, legacy


def test_migrates_schema_1_store_and_keeps_rollback_copy(tmp_path):
    root, canonical, _legacy = _legacy_store(tmp_path)
    old_manifest = (root / SHALLOW_COLLECTION_MANIFEST).read_bytes()
    old_report = (root / SHALLOW_OPERATION_REPORT).read_bytes()

    result = migrate_shallow_store_v1(root)

    manifest = ShallowManifest.from_dict(json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")
    ))
    assert manifest == result.manifest
    assert manifest.collection.images[0].node_id == "image-0"
    assert manifest.collection.labels[0].source_image_id == "image-0"
    assert manifest.bindings.labels[0].component.source is None
    report = validate_report(root, canonical)
    assert report.schema_version == 2
    assert report.output_contract == 2
    assert report.tool_version == __version__
    assert report.manifest == manifest
    assert len(result.report_sha256) == 64
    attrs = json.loads((root / ".zattrs").read_text(encoding="utf-8"))
    assert attrs["biomero"]["format"] == "biomero-shallow-zarr"
    assert "model" not in attrs["biomero"]
    assert (
        result.backup_path / SHALLOW_COLLECTION_MANIFEST
    ).read_bytes() == old_manifest
    assert (
        result.backup_path / SHALLOW_OPERATION_REPORT
    ).read_bytes() == old_report


def test_rejects_schema_1_labels_without_component_bindings(tmp_path):
    _root, _canonical, legacy = _legacy_store(tmp_path)
    legacy["images"][0].pop("labelComponents")

    with pytest.raises(ValueError, match="labelComponent"):
        upgrade_manifest_v1(legacy)


def test_failed_migration_restores_schema_1_files(tmp_path):
    root, _canonical, _legacy = _legacy_store(tmp_path)
    paths = (
        root / SHALLOW_COLLECTION_MANIFEST,
        root / SHALLOW_OPERATION_REPORT,
        root / ".zattrs",
    )
    before = {path: path.read_bytes() for path in paths}

    from biomero_shallower import migration
    original = migration.write_json

    def fail_on_manifest(path, value):
        if Path(path).name == SHALLOW_COLLECTION_MANIFEST:
            raise RuntimeError("injected")
        original(path, value)

    with patch("biomero_shallower.migration.write_json", fail_on_manifest):
        with pytest.raises(RuntimeError, match="injected"):
            migrate_shallow_store_v1(root)

    assert {path: path.read_bytes() for path in paths} == before


def test_cli_exposes_explicit_migration(tmp_path, capsys):
    root, _canonical, _legacy = _legacy_store(tmp_path)

    assert main([
        "migrate-v1", "--returned-zarr", str(root),
    ]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "migrated"
    assert output["manifestSchema"] == 2


def test_upgrades_image_annotation_reference_from_manifest(tmp_path):
    root, _canonical, legacy = _legacy_store(tmp_path)
    manifest = upgrade_manifest_v1(legacy)
    image = legacy["images"][0]
    values = {
        "schema": "1",
        "storageRoot": "group-0-data",
        "relativePath": "results/result.zarr",
        "workflowId": legacy["workflowId"],
        "transferArtifact": legacy["transferArtifact"],
        "imageNodePath": image["imageNodePath"],
        "labelNodePaths": json.dumps(image["labelNodePaths"]),
        "source": json.dumps(image["source"]),
        "interchangeProfile": legacy["interchangeProfile"],
        "model": legacy["model"],
    }

    upgraded = upgrade_annotation_reference_v1(values, manifest)

    reference = ShallowZarrReference.from_annotation_values(upgraded)
    assert reference.schema == 2
    assert reference.format == "biomero-shallow-zarr"
    assert reference.image_node_path == "."
    assert reference.label_node_paths == ("labels/cells",)
    assert "model" not in upgraded


def test_upgrades_plate_annotation_reference_from_manifest(tmp_path):
    _root, _canonical, legacy = _legacy_store(tmp_path)
    legacy["images"][0]["source"]["sourceObjectType"] = "Plate"
    manifest = upgrade_manifest_v1(legacy)
    source = manifest.bindings.images[0].source
    values = {
        "schema": "1",
        "storageRoot": "group-0-data",
        "relativePath": "results/result.zarr",
        "workflowId": legacy["workflowId"],
        "transferArtifact": legacy["transferArtifact"],
        "sourceObjectId": str(source.source_object_id),
        "sourceGeneration": str(source.source_generation),
        "imageNodeCount": "1",
        "interchangeProfile": legacy["interchangeProfile"],
        "model": legacy["model"],
    }

    upgraded = upgrade_annotation_reference_v1(values, manifest)

    reference = ShallowPlateReference.from_annotation_values(upgraded)
    assert reference.schema == 2
    assert reference.format == "biomero-shallow-zarr"
    assert reference.source_object_id == source.source_object_id


def test_rejects_annotation_for_a_different_store_manifest(tmp_path):
    _root, _canonical, legacy = _legacy_store(tmp_path)
    manifest = upgrade_manifest_v1(legacy)
    image = legacy["images"][0]
    values = {
        "schema": "1",
        "storageRoot": "group-0-data",
        "relativePath": "results/result.zarr",
        "workflowId": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "transferArtifact": legacy["transferArtifact"],
        "imageNodePath": image["imageNodePath"],
        "labelNodePaths": json.dumps(image["labelNodePaths"]),
        "source": json.dumps(image["source"]),
        "interchangeProfile": legacy["interchangeProfile"],
        "model": legacy["model"],
    }

    with pytest.raises(ValueError, match="workflowId"):
        upgrade_annotation_reference_v1(values, manifest)


def test_restores_schema_1_store_after_successful_migration(tmp_path):
    root, _canonical, _legacy = _legacy_store(tmp_path)
    paths = (
        root / SHALLOW_COLLECTION_MANIFEST,
        root / SHALLOW_OPERATION_REPORT,
        root / ".zattrs",
    )
    before = {path: path.read_bytes() for path in paths}
    result = migrate_shallow_store_v1(root)

    restore_shallow_store_v1(root, result.backup_path)

    assert {path: path.read_bytes() for path in paths} == before
