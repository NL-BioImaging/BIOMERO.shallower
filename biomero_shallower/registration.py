"""TODO."""

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

from .pixel_identity import (
    PixelIdentityError,
)


from .ngff import join_node_path, node_directory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShallowRegistration:
    """Resolved registration view for a shallow result or label projection."""

    collection_root: Path
    registration_path: Path
    reference: ShallowZarrReference | ShallowPlateReference
    kind: Literal["primary", "label", "plate"]
    plate_label_paths: tuple[tuple[str, Path], ...] = ()
    plate_label_name: str | None = None


def load_managed_storage_roots(
    *,
    import_mount_path: str | Path | None = None,
    config_file: str | Path | None = None,
    group_mappings_file: str | Path | None = None,
) -> dict[str, Path]:
    """Resolve managed roots from the shared runtime group mappings."""
    import_root = Path(
        import_mount_path or os.getenv("IMPORT_MOUNT_PATH", "/data")
    ).resolve()
    if not import_root.is_absolute():
        raise ValueError("IMPORT_MOUNT_PATH must be absolute")

    config_path = Path(config_file or os.getenv(
        "OMERO_BIOMERO_CONFIG_FILE",
        "/auto-importer/config/biomero-config.json",
    ))
    mappings_path = Path(group_mappings_file or os.getenv(
        "OMERO_BIOMERO_GROUP_MAPPINGS_FILE",
        "/auto-importer/config/group-mappings.json",
    ))

    def read_object(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot load managed storage mapping {path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Managed storage mapping must be an object: {path}")
        return value

    legacy = read_object(config_path).get("group_mappings", {})
    if not isinstance(legacy, dict):
        raise ValueError("biomero-config.json group_mappings must be an object")
    mappings = dict(legacy)
    mappings.update(read_object(mappings_path))

    roots = {"import-mount-data": import_root}
    for group_id, mapping in mappings.items():
        if not isinstance(mapping, dict):
            raise ValueError(f"Invalid group mapping for {group_id!r}")
        try:
            normalized_id = int(group_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid group ID {group_id!r}") from exc
        folder = mapping.get("folder")
        if not folder or folder in {".", "root"}:
            root = import_root
        else:
            folder_path = Path(str(folder))
            if folder_path.is_absolute() or ".." in folder_path.parts:
                raise ValueError(
                    f"Group {normalized_id} folder must be relative to IMPORT_MOUNT_PATH"
                )
            root = (import_root / folder_path).resolve()
            try:
                root.relative_to(import_root)
            except ValueError as exc:
                raise ValueError(
                    f"Group {normalized_id} folder escapes IMPORT_MOUNT_PATH"
                ) from exc
        roots[f"group-{normalized_id}-data"] = root
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


def resolve_shallow_registration(
    zarr_path: str | Path,
    *,
    storage_roots: dict[str, Path] | None = None,
    import_mount_path: str | Path | None = None,
    import_options: ZarrImportOptions | dict | None = None,
) -> ShallowRegistration | None:
    """Resolve a primary shallow result or one of its label projections."""
    path = Path(zarr_path).resolve()
    import_root = Path(
        import_mount_path or os.getenv("IMPORT_MOUNT_PATH", "/data")
    ).resolve()
    try:
        path.relative_to(import_root)
    except ValueError:
        return None

    collection_root = None
    for candidate in (path, *path.parents):
        try:
            candidate.relative_to(import_root)
        except ValueError:
            break
        if (candidate / SHALLOW_COLLECTION_MANIFEST).is_file():
            collection_root = candidate
            break
        if candidate == import_root:
            break
    if collection_root is None:
        return None

    try:
        manifest = ShallowManifest.from_dict(json.loads(
            (collection_root / SHALLOW_COLLECTION_MANIFEST).read_text(
                encoding="utf-8"
            )
        ))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PixelIdentityError(
            f"Invalid shallow collection manifest: {collection_root}"
        ) from exc

    options = (
        import_options
        if isinstance(import_options, ZarrImportOptions)
        else ZarrImportOptions.from_dict(import_options or {})
    )
    roots = storage_roots or load_managed_storage_roots(
        import_mount_path=import_root
    )
    relative_node = (
        "." if path == collection_root
        else path.relative_to(collection_root).as_posix()
    )
    if relative_node == ".":
        source_types = {
            binding.source.source_object_type
            for binding in manifest.bindings.images
        }
        if source_types == {"Plate"}:
            reference = ShallowPlateReference.from_manifest(
                manifest,
                storage_root="import-mount-data",
                relative_path=collection_root.relative_to(
                    import_root
                ).as_posix(),
            )
            first_source = manifest.bindings.images[0].source
            registration_path = resolve_managed_source_path(
                first_source,
                roots,
            )
            plate_label_paths = []
            if options.plate_pixel_source == "label":
                label_name = options.plate_label_name
                for plate_image in manifest.collection.images:
                    logical_path = join_node_path(
                        plate_image.node_path,
                        "labels",
                        label_name,
                    )
                    components = [
                        binding.component
                        for binding in manifest.label_bindings_for_image(
                            plate_image.node_id
                        )
                        if binding.component.logical_node_path == logical_path
                    ]
                    if len(components) != 1:
                        raise PixelIdentityError(
                            f"Plate image {plate_image.node_path} does "
                            f"not declare label {label_name!r} exactly once"
                        )
                    component = components[0]
                    if component.source is None:
                        physical_path = node_directory(
                            collection_root,
                            logical_path,
                        )
                    else:
                        physical_root = resolve_managed_source_path(
                            component.source,
                            roots,
                        )
                        physical_path = node_directory(
                            physical_root,
                            component.source.node_path,
                        )
                    if not physical_path.is_dir():
                        raise PixelIdentityError(
                            f"Plate label is unavailable: {physical_path}"
                        )
                    plate_label_paths.append((
                        plate_image.node_path,
                        physical_path,
                    ))
            return ShallowRegistration(
                collection_root=collection_root,
                registration_path=registration_path,
                reference=reference,
                kind="plate",
                plate_label_paths=tuple(plate_label_paths),
                plate_label_name=options.plate_label_name,
            )
        if len(manifest.collection.images) != 1 or source_types != {"Image"}:
            raise PixelIdentityError(
                "Primary registration requires one Image or one Plate source"
            )
        image = manifest.collection.images[0]
        kind = "primary"
    else:
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
        image = matches[0]
        kind = "label"

    reference = ShallowZarrReference.from_manifest(
        manifest,
        storage_root="import-mount-data",
        relative_path=collection_root.relative_to(import_root).as_posix(),
        image_node_path=image.node_path,
    )
    if kind == "label":
        registration_path = path
    else:
        source = manifest.image_binding(image.node_id).source
        registration_path = resolve_managed_source_path(source, roots)
        if source.node_path != ".":
            registration_path = node_directory(
                registration_path,
                source.node_path,
            )
        if not registration_path.is_dir():
            raise PixelIdentityError(
                f"Shallow source image node is unavailable: {registration_path}"
            )
    return ShallowRegistration(
        collection_root=collection_root,
        registration_path=registration_path,
        reference=reference,
        kind=kind,
    )
