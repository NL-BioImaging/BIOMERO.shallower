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
"""Explicit one-time migration of prerelease shallow stores to schema 2."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Literal
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
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrLabelComponent,
)

from . import __version__
from .pixel_identity import (
    IsccBioIdentityProvider,
    read_zarr_v2_semantic_guard,
)
from .filesystem import write_json


class _LegacyModel(BaseModel):
    """Frozen, strict base of the schema-1 models, keyed by their JSON aliases."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        validate_by_alias=True,
    )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize to a JSON-compatible dict using the aliases.

        Returns:
            dict[str, Any]:
                The model as JSON-compatible data.
        """
        return self.model_dump(by_alias=True, mode="json")


class _LegacyImageReference(_LegacyModel):
    """
    One image entry of a schema-1 shallow collection.

    Attributes:
        image_node_path (str): The image node path.
        source (CanonicalZarrSource): The canonical source of the image.
        returned_pixel_identity (PixelIdentity): The identity of the returned image.
        label_node_paths (tuple[str, ...]): The retained label node paths.
        label_components (tuple[ZarrLabelComponent, ...]): The label bindings;
            may be empty in early schema-1 writers.
    """

    image_node_path: str = Field(alias="imageNodePath")
    source: CanonicalZarrSource
    returned_pixel_identity: PixelIdentity = Field(alias="returnedPixelIdentity")
    label_node_paths: tuple[str, ...] = Field(alias="labelNodePaths")
    label_components: tuple[ZarrLabelComponent, ...] = Field(
        default_factory=tuple,
        alias="labelComponents",
    )

    @model_validator(mode="after")
    def validate_labels(self) -> "_LegacyImageReference":
        """
        Require one label component per label node path when components exist.

        Returns:
            _LegacyImageReference:
                The validated model.

        Raises:
            ValueError: If the component paths differ from ``label_node_paths``.
        """
        component_paths = tuple(
            component.logical_node_path for component in self.label_components
        )
        # Schema 1 allowed path-only label inventories. Complete component
        # bindings were validated only when the optional list was populated.
        if component_paths and set(component_paths) != set(self.label_node_paths):
            raise ValueError(
                "schema-1 migration requires one labelComponent for every "
                "labelNodePath"
            )
        return self


class _LegacyShallowCollection(_LegacyModel):
    """
    The schema-1 shallow collection document.

    Attributes:
        workflow_id (UUID): The workflow the store belongs to.
        transfer_artifact (str): The transfer artifact name.
        images (tuple[_LegacyImageReference, ...]): The image entries.
        interchange_profile (str): The interchange profile.
        model (Literal["rfc8-shallow-copy"]): The shallow model name.
        schema_version (Literal[1]): The schema version.
    """

    workflow_id: UUID = Field(alias="workflowId")
    transfer_artifact: str = Field(alias="transferArtifact")
    images: tuple[_LegacyImageReference, ...] = Field(min_length=1)
    interchange_profile: str = Field(alias="interchangeProfile")
    model: Literal["rfc8-shallow-copy"]
    schema_version: Literal[1] = Field(alias="schema")


@dataclass(frozen=True)
class ShallowMigrationResult:
    """
    Completed filesystem migration and its retained rollback copy.

    Attributes:
        store_path (Path): The migrated store.
        backup_path (Path): The rollback copy of the replaced metadata.
        manifest (ShallowManifest): The new manifest.
        report_sha256 (str | None): The checksum of the new operation report, or
            None if the store has no report.
    """

    store_path: Path
    backup_path: Path
    manifest: ShallowManifest
    report_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize the result for the CLI.

        Returns:
            dict[str, Any]:
                The migration result document.
        """
        return {
            "schema": 1,
            "result": "migrated",
            "store": str(self.store_path),
            "backup": str(self.backup_path),
            "manifestSchema": self.manifest.schema,
            "format": self.manifest.format,
            "reportSha256": self.report_sha256,
        }


def _replace_canonical_locators(value: Any, replacements: dict[tuple[str, str], str]) -> Any:
    """
    Replace managed canonical paths in a JSON-compatible contract tree.

    Args:
        value (Any): A JSON-compatible value (dict, list or scalar).
        replacements (dict[tuple[str, str], str]): New relative paths keyed by
            (storage root, old relative path).

    Returns:
        Any:
            The tree with matching locators replaced (and their
            ``sourceGeneration`` reset to 1).
    """
    if isinstance(value, dict):
        updated = {
            key: _replace_canonical_locators(item, replacements)
            for key, item in value.items()
        }
        locator = (updated.get("storageRoot"), updated.get("relativePath"))
        if locator in replacements:
            updated["relativePath"] = replacements[locator]
            if "sourceGeneration" in updated:
                updated["sourceGeneration"] = 1
        return updated
    if isinstance(value, list):
        return [_replace_canonical_locators(item, replacements) for item in value]
    return value


def rebind_shallow_store_sources(
    store_path: str | Path,
    replacements: dict[tuple[str, str], str],
    *,
    backup_path: str | Path,
) -> ShallowMigrationResult:
    """
    Point one settled shallow store at stable canonical locations.

    Only versioned metadata is rewritten. Pixel chunks in the shallow result
    are untouched. The manifest and operation report are validated together,
    and both are restored if either write fails.

    Args:
        store_path (str | Path): The shallow store directory.
        replacements (dict[tuple[str, str], str]): New relative paths keyed by
            (storage root, old relative path).
        backup_path (str | Path): Where to keep the rollback copy; must not exist
            and must be outside the store.

    Returns:
        ShallowMigrationResult:
            The migration result.

    Raises:
        ValueError: If the store is not a real directory, the replacements are
            missing, empty or match nothing, the report and manifest would
            diverge, or the backup location is invalid.
    """
    root = Path(store_path).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Shallow store must be a real directory: {root}")
    if not replacements:
        raise ValueError("At least one canonical locator replacement is required")
    normalized = {
        (str(storage_root), str(old_path)): str(new_path)
        for (storage_root, old_path), new_path in replacements.items()
    }
    if any(not root_name or not old or not new
           for (root_name, old), new in normalized.items()):
        raise ValueError("Canonical locator replacements cannot be empty")

    manifest_path = root / SHALLOW_COLLECTION_MANIFEST
    manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = ShallowManifest.from_dict(
        _replace_canonical_locators(manifest_raw, normalized)
    )
    if manifest.to_dict() == ShallowManifest.from_dict(manifest_raw).to_dict():
        raise ValueError(f"Shallow store does not reference the canonical paths: {root}")

    report_path = root / SHALLOW_OPERATION_REPORT
    report = None
    if report_path.is_file():
        report_raw = json.loads(report_path.read_text(encoding="utf-8"))
        report = ShallowOperationReport.from_dict(
            _replace_canonical_locators(report_raw, normalized)
        )
        if report.manifest != manifest:
            raise ValueError("Shallow operation report and manifest would diverge")

    backup = Path(backup_path).resolve()
    if backup == root or backup.is_relative_to(root):
        raise ValueError("Migration backup must be outside the shallow store")
    if backup.exists():
        raise ValueError(f"Migration backup already exists: {backup}")
    files = [manifest_path]
    if report is not None:
        files.append(report_path)
    backup.mkdir(parents=True)
    for source in files:
        shutil.copy2(source, backup / source.name)

    try:
        write_json(manifest_path, manifest.to_dict())
        if report is not None:
            write_json(report_path, report.to_dict())
        ShallowManifest.from_dict(json.loads(
            manifest_path.read_text(encoding="utf-8")
        ))
        if report is not None:
            persisted_report = ShallowOperationReport.from_dict(json.loads(
                report_path.read_text(encoding="utf-8")
            ))
            if persisted_report.manifest != manifest:
                raise ValueError("Persisted shallow report and manifest diverge")
    except BaseException:
        for source in files:
            shutil.copy2(backup / source.name, source)
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


def _reconstruct_label_components(
    root: Path, image: _LegacyImageReference, identity_provider: Any
) -> tuple[ZarrLabelComponent, ...]:
    """
    Rebuild the label bindings of a path-only schema-1 image from its stored labels.

    Args:
        root (Path): The store directory.
        image (_LegacyImageReference): The legacy image entry.
        identity_provider (Any): Provider whose ``generate`` hashes a label node.

    Returns:
        tuple[ZarrLabelComponent, ...]:
            One component per label node path.
    """
    components = []
    for node_path in image.label_node_paths:
        guard = read_zarr_v2_semantic_guard(root, node_path)
        identity = identity_provider.generate(
            root,
            node_path=node_path,
            role="label",
            shape=guard.shape,
            dtype=guard.dtype,
            axes=guard.axes,
            coordinate_transformations=guard.coordinate_transformations,
        )
        components.append(ZarrLabelComponent(
            logicalNodePath=node_path,
            pixelIdentity=identity,
        ))
    return tuple(components)


def upgrade_manifest_v1(
    value: dict,
    *,
    store_path: str | Path | None = None,
    identity_provider: Any = None,
) -> ShallowManifest:
    """
    Convert a validated prerelease schema-1 manifest in memory.

    Early schema-1 writers recorded only ``labelNodePaths``. Those labels were
    retained in the shallow store, so migration reconstructs their missing
    identity bindings from the physical nodes when ``store_path`` is supplied.

    Args:
        value (dict): The schema-1 manifest as JSON data.
        store_path (str | Path, optional): The store holding the label nodes;
            required when labels are path-only.
        identity_provider (Any, optional): Provider used to hash reconstructed
            labels; defaults to an ``IsccBioIdentityProvider``.

    Returns:
        ShallowManifest:
            The schema-2 manifest.

    Raises:
        ValueError: If the manifest is invalid, or has path-only labels and no
            ``store_path``.
    """
    legacy = _LegacyShallowCollection.model_validate(value)
    missing_components = any(
        image.label_node_paths and not image.label_components
        for image in legacy.images
    )
    root = Path(store_path).resolve() if store_path is not None else None
    if missing_components and root is None:
        raise ValueError(
            "schema-1 manifest has path-only labels; store_path is required "
            "to reconstruct labelComponents"
        )
    provider = identity_provider
    if missing_components and provider is None:
        provider = IsccBioIdentityProvider()
    components_by_image = tuple(
        image.label_components
        or _reconstruct_label_components(root, image, provider)
        for image in legacy.images
    )
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
            for image_node, components in zip(image_nodes, components_by_image)
            for component in components
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


def upgrade_annotation_reference_v1(
    values: dict[str, str],
    manifest: ShallowManifest,
) -> dict[str, str]:
    """
    Upgrade one validated schema-1 OMERO shallow-reference projection.

    Args:
        values (dict[str, str]): The schema-1 annotation values.
        manifest (ShallowManifest): The upgraded store manifest they must match.

    Returns:
        dict[str, str]:
            The schema-2 annotation values.

    Raises:
        ValueError: If the values are not a schema-1 shallow reference, are of
            an unknown kind, or do not match the store manifest.
    """
    if values.get("schema") != "1" or values.get("model") != "rfc8-shallow-copy":
        raise ValueError("Expected a schema-1 shallow OMERO reference")
    expected = {
        "workflowId": str(manifest.workflow_id),
        "transferArtifact": manifest.transfer_artifact,
        "interchangeProfile": manifest.interchange_profile,
    }
    for key, value in expected.items():
        if values.get(key) != value:
            raise ValueError(f"OMERO reference {key} does not match the store manifest")

    common = {
        "storage_root": values["storageRoot"],
        "relative_path": values["relativePath"],
    }
    if "imageNodePath" in values:
        labels = tuple(json.loads(values["labelNodePaths"]))
        reference = ShallowZarrReference.from_manifest(
            manifest,
            image_node_path=values["imageNodePath"],
            label_node_paths=labels,
            **common,
        )
        old_source = CanonicalZarrSource.from_dict(json.loads(values["source"]))
        if reference.source != old_source:
            raise ValueError("OMERO reference source does not match the store manifest")
    elif "sourceObjectId" in values:
        reference = ShallowPlateReference.from_manifest(manifest, **common)
        old_projection = (
            int(values["sourceObjectId"]),
            int(values["sourceGeneration"]),
            int(values["imageNodeCount"]),
        )
        new_projection = (
            reference.source_object_id,
            reference.source_generation,
            reference.image_node_count,
        )
        if old_projection != new_projection:
            raise ValueError(
                "OMERO Plate reference does not match the store manifest"
            )
    else:
        raise ValueError("Unknown schema-1 shallow OMERO reference kind")
    return reference.to_annotation_values()


def _upgrade_report_v1(
    value: dict,
    manifest: ShallowManifest,
    legacy_manifest: dict,
) -> ShallowOperationReport:
    """
    Convert a schema-1 operation report, checking it against the legacy manifest.

    Args:
        value (dict): The schema-1 report as JSON data.
        manifest (ShallowManifest): The upgraded manifest to embed.
        legacy_manifest (dict): The schema-1 sidecar manifest as JSON data.

    Returns:
        ShallowOperationReport:
            The schema-2 report.

    Raises:
        ValueError: If the report is not schema 1 or its collection differs
            from the sidecar manifest.
    """
    if value.get("schema") != 1 or value.get("outputContract") != 1:
        raise ValueError("Expected a schema-1 shallow operation report")
    legacy_collection = _LegacyShallowCollection.model_validate(
        value.get("collection")
    )
    expected_collection = _LegacyShallowCollection.model_validate(
        legacy_manifest
    )
    if legacy_collection != expected_collection:
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
    identity_provider: Any = None,
) -> ShallowMigrationResult:
    """
    Upgrade one settled schema-1 shallow store and retain rollback files.

    This command is for prerelease data that is no longer part of an active
    transfer or import. Rewriting the operation report changes its checksum;
    any in-flight remote receipt therefore becomes invalid.

    Args:
        store_path (str | Path): The shallow store directory.
        backup_path (str | Path, optional): Where to keep the rollback copy;
            defaults to a hidden sibling of the store.
        identity_provider (Any, optional): Provider used to hash reconstructed
            labels.

    Returns:
        ShallowMigrationResult:
            The migration result.

    Raises:
        ValueError: If the store is not a real directory, its BIOMERO shallow
            metadata is missing or not schema 1, or the backup location is
            invalid.
    """
    root = Path(store_path).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Shallow store must be a real directory: {root}")
    manifest_path = root / SHALLOW_COLLECTION_MANIFEST
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = upgrade_manifest_v1(
        raw_manifest,
        store_path=root,
        identity_provider=identity_provider,
    )

    report_path = root / SHALLOW_OPERATION_REPORT
    report = None
    if report_path.is_file():
        report = _upgrade_report_v1(
            json.loads(report_path.read_text(encoding="utf-8")),
            manifest,
            raw_manifest,
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


def restore_shallow_store_v1(
    store_path: str | Path,
    backup_path: str | Path,
) -> None:
    """
    Restore schema-1 metadata after a coordinated migration failure.

    Args:
        store_path (str | Path): The shallow store directory.
        backup_path (str | Path): The rollback copy made by the migration.

    Raises:
        ValueError: If the store or backup is not a real directory, the backup
            is inside the store, does not match the current store, or is
            incomplete.
    """
    root = Path(store_path).resolve()
    backup = Path(backup_path).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Shallow store must be a real directory: {root}")
    if not backup.is_dir() or backup.is_symlink():
        raise ValueError(f"Migration backup must be a real directory: {backup}")
    if backup == root or backup.is_relative_to(root):
        raise ValueError("Migration backup must be outside the shallow store")

    legacy_path = backup / SHALLOW_COLLECTION_MANIFEST
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    expected_current = upgrade_manifest_v1(legacy, store_path=root)
    current = ShallowManifest.from_dict(json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")
    ))
    if current != expected_current:
        raise ValueError("Migration backup does not match the current shallow store")

    relative_files = [Path(SHALLOW_COLLECTION_MANIFEST)]
    if (backup / SHALLOW_OPERATION_REPORT).is_file():
        relative_files.append(Path(SHALLOW_OPERATION_REPORT))
    legacy_collection = _LegacyShallowCollection.model_validate(legacy)
    relative_files.extend(
        (Path(image.image_node_path) if image.image_node_path != "." else Path())
        / ".zattrs"
        for image in legacy_collection.images
    )
    for relative in relative_files:
        source = backup / relative
        if not source.is_file():
            raise ValueError(f"Incomplete migration backup: {source}")
    for relative in relative_files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / relative, target)


__all__ = [
    "ShallowMigrationResult",
    "migrate_shallow_store_v1",
    "rebind_shallow_store_sources",
    "restore_shallow_store_v1",
    "upgrade_annotation_reference_v1",
    "upgrade_manifest_v1",
]
