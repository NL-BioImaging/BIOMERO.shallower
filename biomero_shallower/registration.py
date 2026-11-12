"""Resolve managed storage roots and the registration view of a shallow result.

A shallow result registers in OMERO through its canonical pixels (or through
one of its label projections), so registration needs the managed storage roots
and the collection manifest to find them.
"""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Literal

from biomero_schema.zarr import (
    SHALLOW_COLLECTION_MANIFEST,
    ShallowManifest,
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrImportOptions,
)

from .ngff import LABELS_GROUP_NAME, PixelIdentityError, join_node_path, node_directory

__all__ = [
    "ShallowRegistration",
    "load_managed_storage_roots",
    "read_collection_manifest",
    "resolve_managed_source_path",
    "resolve_shallow_registration",
]

logger = logging.getLogger(__name__)

# The import mount is managed storage root "import-mount-data"; each OMERO group adds
# "group-<id>-data" below it.
IMPORT_MOUNT_ENV = "IMPORT_MOUNT_PATH"
DEFAULT_IMPORT_MOUNT_PATH = "/data"
IMPORT_MOUNT_ROOT_NAME = "import-mount-data"
CONFIG_FILE_ENV = "OMERO_BIOMERO_CONFIG_FILE"
DEFAULT_CONFIG_FILE = "/auto-importer/config/biomero-config.json"
GROUP_MAPPINGS_FILE_ENV = "OMERO_BIOMERO_GROUP_MAPPINGS_FILE"
DEFAULT_GROUP_MAPPINGS_FILE = "/auto-importer/config/group-mappings.json"
# Folder values of a group mapping that mean "the import mount itself".
ROOT_FOLDER_NAMES = {".", "root"}


@dataclass(frozen=True)
class ShallowRegistration:
    """Resolved registration view for a shallow result or label projection."""

    collection_root: Path
    registration_path: Path
    reference: ShallowZarrReference | ShallowPlateReference
    kind: Literal["primary", "label", "plate"]
    plate_label_paths: tuple[tuple[str, Path], ...] = ()
    plate_label_name: str | None = None


def _import_mount_root(import_mount_path: str | Path | None) -> Path:
    """Return the import mount, from the argument or the environment."""
    return Path(
        import_mount_path or os.getenv(IMPORT_MOUNT_ENV, DEFAULT_IMPORT_MOUNT_PATH)
    ).resolve()


def _read_mapping_file(path: Path) -> dict:
    """Read a JSON object file; a missing file reads as empty."""
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load managed storage mapping {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Managed storage mapping must be an object: {path}")
    return value


def _group_root(import_root: Path, group_id: int, mapping: dict) -> Path:
    """Return a group's data folder, which must stay inside the import mount."""
    folder = mapping.get("folder")
    if not folder or folder in ROOT_FOLDER_NAMES:
        return import_root
    folder_path = Path(str(folder))
    if folder_path.is_absolute() or ".." in folder_path.parts:
        raise ValueError(
            f"Group {group_id} folder must be relative to {IMPORT_MOUNT_ENV}"
        )
    root = (import_root / folder_path).resolve()
    try:
        root.relative_to(import_root)
    except ValueError as exc:
        raise ValueError(
            f"Group {group_id} folder escapes {IMPORT_MOUNT_ENV}"
        ) from exc
    return root


def load_managed_storage_roots(
    *,
    import_mount_path: str | Path | None = None,
    config_file: str | Path | None = None,
    group_mappings_file: str | Path | None = None,
) -> dict[str, Path]:
    """Resolve managed roots from the shared runtime group mappings."""
    import_root = _import_mount_root(import_mount_path)
    if not import_root.is_absolute():
        raise ValueError(f"{IMPORT_MOUNT_ENV} must be absolute")
    config_path = Path(config_file or os.getenv(CONFIG_FILE_ENV, DEFAULT_CONFIG_FILE))
    mappings_path = Path(group_mappings_file or os.getenv(
        GROUP_MAPPINGS_FILE_ENV, DEFAULT_GROUP_MAPPINGS_FILE))

    legacy = _read_mapping_file(config_path).get("group_mappings", {})
    if not isinstance(legacy, dict):
        raise ValueError("biomero-config.json group_mappings must be an object")
    mappings = dict(legacy)
    mappings.update(_read_mapping_file(mappings_path))

    roots = {IMPORT_MOUNT_ROOT_NAME: import_root}
    for group_id, mapping in mappings.items():
        if not isinstance(mapping, dict):
            raise ValueError(f"Invalid group mapping for {group_id!r}")
        try:
            normalized_id = int(group_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid group ID {group_id!r}") from exc
        roots[f"group-{normalized_id}-data"] = _group_root(
            import_root, normalized_id, mapping)
    return roots


def resolve_managed_source_path(
    source,
    storage_roots: dict[str, Path],
) -> Path:
    """Resolve a canonical source without allowing its locator to escape."""
    root = storage_roots.get(source.storage_root)
    if root is None:
        raise ValueError(f"Unknown managed storage root {source.storage_root!r}")
    root = Path(root).resolve()
    candidate = (root / Path(source.relative_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Canonical source escapes storage root {source.storage_root}"
        ) from exc
    if not candidate.is_dir():
        raise ValueError(f"Canonical source is unavailable: {candidate}")
    return candidate


def read_collection_manifest(collection_root: Path) -> ShallowManifest:
    """Read the shallow collection manifest stored in ``collection_root``."""
    manifest_path = collection_root / SHALLOW_COLLECTION_MANIFEST
    try:
        return ShallowManifest.from_dict(json.loads(
            manifest_path.read_text(encoding="utf-8")
        ))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PixelIdentityError(
            f"Invalid shallow collection manifest: {manifest_path}"
        ) from exc


def _find_collection_root(path: Path, import_root: Path) -> Path | None:
    """Return the nearest folder at or above ``path`` (inside the mount) with a manifest."""
    for candidate in (path, *path.parents):
        try:
            candidate.relative_to(import_root)
        except ValueError:
            break
        if (candidate / SHALLOW_COLLECTION_MANIFEST).is_file():
            return candidate
        if candidate == import_root:
            break
    return None


def _source_directory(source, roots: dict[str, Path]) -> Path:
    """Return the directory of a canonical source's own node."""
    source_root = resolve_managed_source_path(source, roots)
    if source.node_path == ".":
        return source_root
    return node_directory(source_root, source.node_path)


def _plate_label_paths(manifest, collection_root, roots, label_name):
    """Return ``(image node path, label directory)`` for the label every Plate image declares."""
    label_paths = []
    for plate_image in manifest.collection.images:
        logical_path = join_node_path(plate_image.node_path, LABELS_GROUP_NAME, label_name)
        components = [
            binding.component
            for binding in manifest.label_bindings_for_image(plate_image.node_id)
            if binding.component.logical_node_path == logical_path
        ]
        if len(components) != 1:
            raise PixelIdentityError(
                f"Plate image {plate_image.node_path} does "
                f"not declare label {label_name!r} exactly once"
            )
        component = components[0]
        if component.source is None:
            physical_path = node_directory(collection_root, logical_path)
        else:
            physical_path = _source_directory(component.source, roots)
        if not physical_path.is_dir():
            raise PixelIdentityError(f"Plate label is unavailable: {physical_path}")
        label_paths.append((plate_image.node_path, physical_path))
    return tuple(label_paths)


def _plate_registration(manifest, collection_root, import_root, roots, options):
    """Registration view of a Plate: its first source image, optionally with a label."""
    reference = ShallowPlateReference.from_manifest(
        manifest,
        storage_root=IMPORT_MOUNT_ROOT_NAME,
        relative_path=collection_root.relative_to(import_root).as_posix(),
    )
    first_source = manifest.bindings.images[0].source
    plate_label_paths = ()
    if options.plate_pixel_source == "label":
        plate_label_paths = _plate_label_paths(
            manifest, collection_root, roots, options.plate_label_name)
    return ShallowRegistration(
        collection_root=collection_root,
        registration_path=resolve_managed_source_path(first_source, roots),
        reference=reference,
        kind="plate",
        plate_label_paths=plate_label_paths,
        plate_label_name=options.plate_label_name,
    )


def _primary_image(manifest, source_types):
    """The single image of a primary (non-Plate) shallow result, or raise."""
    if len(manifest.collection.images) != 1 or source_types != {"Image"}:
        raise PixelIdentityError(
            "Primary registration requires one Image or one Plate source"
        )
    return manifest.collection.images[0]


def _image_of_label(manifest, relative_node, path):
    """The collection image owning the label projection at ``relative_node``, or raise."""
    matches = [
        image for image in manifest.collection.images
        if relative_node in {
            label.node_path for label in manifest.collection.labels
            if label.source_image_id == image.node_id
        }
    ]
    if len(matches) != 1:
        raise PixelIdentityError(
            f"Zarr path is not a declared shallow label: {path}"
        )
    return matches[0]


def _primary_registration_path(manifest, image, roots):
    """The canonical source node a primary shallow result registers through."""
    source = manifest.image_binding(image.node_id).source
    registration_path = _source_directory(source, roots)
    if not registration_path.is_dir():
        raise PixelIdentityError(
            f"Shallow source image node is unavailable: {registration_path}"
        )
    return registration_path


def resolve_shallow_registration(
    zarr_path: str | Path,
    *,
    storage_roots: dict[str, Path] | None = None,
    import_mount_path: str | Path | None = None,
    import_options: ZarrImportOptions | dict | None = None,
) -> ShallowRegistration | None:
    """Resolve a primary shallow result or one of its label projections.

    Returns None when ``zarr_path`` is not inside a shallow collection.
    """
    path = Path(zarr_path).resolve()
    import_root = _import_mount_root(import_mount_path)
    try:
        path.relative_to(import_root)
    except ValueError:
        return None
    collection_root = _find_collection_root(path, import_root)
    if collection_root is None:
        return None

    manifest = read_collection_manifest(collection_root)
    options = (
        import_options
        if isinstance(import_options, ZarrImportOptions)
        else ZarrImportOptions.from_dict(import_options or {})
    )
    roots = storage_roots or load_managed_storage_roots(import_mount_path=import_root)
    is_collection_root = path == collection_root
    source_types = {
        binding.source.source_object_type for binding in manifest.bindings.images
    }
    if is_collection_root and source_types == {"Plate"}:
        return _plate_registration(manifest, collection_root, import_root, roots, options)

    if is_collection_root:
        image = _primary_image(manifest, source_types)
        kind = "primary"
        registration_path = _primary_registration_path(manifest, image, roots)
    else:
        relative_node = path.relative_to(collection_root).as_posix()
        image = _image_of_label(manifest, relative_node, path)
        kind = "label"
        registration_path = path
    return ShallowRegistration(
        collection_root=collection_root,
        registration_path=registration_path,
        reference=ShallowZarrReference.from_manifest(
            manifest,
            storage_root=IMPORT_MOUNT_ROOT_NAME,
            relative_path=collection_root.relative_to(import_root).as_posix(),
            image_node_path=image.node_path,
        ),
        kind=kind,
    )
