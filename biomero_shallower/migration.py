"""Explicit one-time migration of prerelease shallow stores to schema 2."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from biomero_schema.shallower import (
    SHALLOW_OPERATION_REPORT,
    ShallowOperationReport,
)
from biomero_schema.zarr import (
    SHALLOW_COLLECTION_MANIFEST,
    CanonicalZarrSource,
    PixelIdentity,
    ShallowBindings,
    ShallowCollection,
    ShallowImageBinding,
    ShallowImageNode,
    ShallowLabelBinding,
    ShallowLabelNode,
    ShallowManifest,
    ZarrLabelComponent,
)

from . import __version__
from .transaction import write_json


class _LegacyModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        validate_by_alias=True,
    )

    def to_dict(self):
        return self.model_dump(by_alias=True, mode="json")


class _LegacyImageReference(_LegacyModel):
    image_node_path: str = Field(alias="imageNodePath")
    source: CanonicalZarrSource
    returned_pixel_identity: PixelIdentity = Field(alias="returnedPixelIdentity")
    label_node_paths: tuple[str, ...] = Field(alias="labelNodePaths")
    label_components: tuple[ZarrLabelComponent, ...] = Field(
        default_factory=tuple,
        alias="labelComponents",
    )

    @model_validator(mode="after")
    def validate_labels(self):
        component_paths = tuple(
            component.logical_node_path for component in self.label_components
        )
        if set(component_paths) != set(self.label_node_paths):
            raise ValueError(
                "schema-1 migration requires one labelComponent for every "
                "labelNodePath"
            )
        return self


class _LegacyShallowCollection(_LegacyModel):
    workflow_id: UUID = Field(alias="workflowId")
    transfer_artifact: str = Field(alias="transferArtifact")
    images: tuple[_LegacyImageReference, ...] = Field(min_length=1)
    interchange_profile: str = Field(alias="interchangeProfile")
    model: Literal["rfc8-shallow-copy"]
    schema_version: Literal[1] = Field(alias="schema")


@dataclass(frozen=True)
class ShallowMigrationResult:
    """Completed filesystem migration and its retained rollback copy."""

    store_path: Path
    backup_path: Path
    manifest: ShallowManifest
    report_sha256: str | None

    def to_dict(self):
        return {
            "schema": 1,
            "result": "migrated",
            "store": str(self.store_path),
            "backup": str(self.backup_path),
            "manifestSchema": self.manifest.schema,
            "format": self.manifest.format,
            "reportSha256": self.report_sha256,
        }


def upgrade_manifest_v1(value: dict) -> ShallowManifest:
    """Convert a validated prerelease schema-1 manifest in memory."""
    legacy = _LegacyShallowCollection.model_validate(value)
    image_nodes = tuple(
        ShallowImageNode(
            id=f"image-{index}",
            name=image.image_node_path,
            nodePath=image.image_node_path,
        )
        for index, image in enumerate(legacy.images)
    )
    label_records = tuple(
        (f"label-{index}", image_node, component)
        for index, (image_node, component) in enumerate(
            (image_node, component)
            for image_node, image in zip(image_nodes, legacy.images)
            for component in image.label_components
        )
    )
    return ShallowManifest(
        workflowId=legacy.workflow_id,
        transferArtifact=legacy.transfer_artifact,
        interchangeProfile=legacy.interchange_profile,
        collection=ShallowCollection(
            name=legacy.transfer_artifact,
            images=image_nodes,
            labels=tuple(
                ShallowLabelNode(
                    id=label_id,
                    name=component.logical_node_path,
                    nodePath=component.logical_node_path,
                    sourceImageId=image_node.node_id,
                )
                for label_id, image_node, component in label_records
            ),
        ),
        bindings=ShallowBindings(
            images=tuple(
                ShallowImageBinding(
                    nodeId=image_node.node_id,
                    source=image.source,
                    returnedPixelIdentity=image.returned_pixel_identity,
                )
                for image_node, image in zip(image_nodes, legacy.images)
            ),
            labels=tuple(
                ShallowLabelBinding(nodeId=label_id, component=component)
                for label_id, _image_node, component in label_records
            ),
        ),
    )


def _upgrade_report_v1(value: dict, manifest: ShallowManifest):
    if value.get("schema") != 1 or value.get("outputContract") != 1:
        raise ValueError("Expected a schema-1 shallow operation report")
    legacy_collection = _LegacyShallowCollection.model_validate(
        value.get("collection")
    )
    if upgrade_manifest_v1(legacy_collection.to_dict()) != manifest:
        raise ValueError("Schema-1 report and sidecar manifests differ")
    upgraded = dict(value)
    upgraded.pop("collection")
    upgraded.update({
        "schema": 2,
        "outputContract": 2,
        "toolVersion": __version__,
        "manifest": manifest.to_dict(),
    })
    return ShallowOperationReport.from_dict(upgraded)


def migrate_shallow_store_v1(
    store_path: str | Path,
    *,
    backup_path: str | Path | None = None,
) -> ShallowMigrationResult:
    """Upgrade one settled schema-1 shallow store and retain rollback files.

    This command is for prerelease data that is no longer part of an active
    transfer or import. Rewriting the operation report changes its checksum;
    any in-flight remote receipt therefore becomes invalid.
    """
    root = Path(store_path).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Shallow store must be a real directory: {root}")
    manifest_path = root / SHALLOW_COLLECTION_MANIFEST
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = upgrade_manifest_v1(raw_manifest)

    report_path = root / SHALLOW_OPERATION_REPORT
    report = None
    if report_path.is_file():
        report = _upgrade_report_v1(
            json.loads(report_path.read_text(encoding="utf-8")),
            manifest,
        )

    attrs_updates = {}
    for image in manifest.collection.images:
        node = root if image.node_path == "." else root / image.node_path
        attrs_path = node / ".zattrs"
        attrs = json.loads(attrs_path.read_text(encoding="utf-8"))
        biomero = attrs.get("biomero")
        if not isinstance(biomero, dict):
            raise ValueError(f"Missing BIOMERO shallow metadata: {attrs_path}")
        if biomero.get("manifest") != SHALLOW_COLLECTION_MANIFEST:
            raise ValueError(f"Unexpected shallow manifest link: {attrs_path}")
        if biomero.get("model") != "rfc8-shallow-copy":
            raise ValueError(f"Expected schema-1 shallow model: {attrs_path}")
        biomero = dict(biomero)
        biomero.pop("model")
        biomero["format"] = manifest.format
        attrs = dict(attrs)
        attrs["biomero"] = biomero
        attrs_updates[attrs_path] = attrs

    backup = Path(backup_path).resolve() if backup_path else root.with_name(
        f".{root.name}.biomero-schema1-backup"
    )
    if backup == root or backup.is_relative_to(root):
        raise ValueError("Migration backup must be outside the shallow store")
    if backup.exists():
        raise ValueError(f"Migration backup already exists: {backup}")
    source_files = [manifest_path, *attrs_updates]
    if report is not None:
        source_files.append(report_path)
    backup.mkdir(parents=True)
    for source in source_files:
        target = backup / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    try:
        for path, attrs in attrs_updates.items():
            write_json(path, attrs)
        write_json(manifest_path, manifest.to_dict())
        if report is not None:
            write_json(report_path, report.to_dict())
        ShallowManifest.from_dict(json.loads(
            manifest_path.read_text(encoding="utf-8")
        ))
        if report is not None:
            ShallowOperationReport.from_dict(json.loads(
                report_path.read_text(encoding="utf-8")
            ))
    except BaseException:
        for source in source_files:
            shutil.copy2(backup / source.relative_to(root), source)
        raise

    report_sha256 = (
        hashlib.sha256(report_path.read_bytes()).hexdigest()
        if report is not None else None
    )
    return ShallowMigrationResult(
        store_path=root,
        backup_path=backup,
        manifest=manifest,
        report_sha256=report_sha256,
    )


__all__ = [
    "ShallowMigrationResult",
    "migrate_shallow_store_v1",
    "upgrade_manifest_v1",
]
