"""Rebuild a conventional image or Plate Zarr from a shallow reference.

The managed canonical pixels and the managed labels are copied into a sibling
staging directory, which is renamed into place only after its label inventory
matches the shallow collection. The managed sources are never modified.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
from uuid import uuid4

from biomero_schema.zarr import (
    ManagedZarrNode,
    ShallowManifest,
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrLabelComponent,
)

from .discovery import discover_ngff_nodes
from .ngff import group_metadata, join_node_path, node_directory, write_labels_group
from .pixel_identity import PixelIdentityError
from .registration import read_collection_manifest, resolve_managed_source_path

__all__ = ["MaterializedShallowResult", "materialize_shallow_zarr"]

logger = logging.getLogger(__name__)

# Written by the importer into canonical stores; never copied into a materialized Zarr.
CANONICAL_MARKER_NAME = ".biomero-canonical.json"
LABELS_GROUP_NAME = "labels"


@dataclass(frozen=True)
class MaterializedShallowResult:
    """A full temporary workflow input reconstructed from managed components."""

    destination: Path
    manifest: ShallowManifest
    labels: tuple[ZarrLabelComponent, ...]


def materialize_shallow_zarr(
    reference: ShallowZarrReference | ShallowPlateReference,
    destination: str | Path,
    storage_roots: dict[str, Path],
    *,
    move=None,
    replace=None,
) -> MaterializedShallowResult:
    """Build a conventional image or Plate Zarr from a shallow reference.

    The managed source and collections are read-only. The destination is
    assembled as a sibling staging directory and atomically renamed only after
    every declared label has been copied and its stable source recorded.

    ``move`` renames the staging directory into place (default ``os.replace``;
    tests inject failures). ``replace`` is the deprecated former name
    of ``move``, still accepted for existing callers.
    """
    move = replace or move or os.replace
    destination = Path(destination)
    if destination.exists():
        raise PixelIdentityError(
            f"Shallow materialization destination already exists: {destination}"
        )
    collection_root = resolve_managed_source_path(reference, storage_roots)
    manifest = read_collection_manifest(collection_root)
    if isinstance(reference, ShallowPlateReference):
        return _materialize_plate(reference, destination, storage_roots, move,
                                  collection_root, manifest)
    return _materialize_image(reference, destination, storage_roots, move,
                              collection_root, manifest)


@contextmanager
def _staging_directory(destination: Path):
    """Yield a sibling staging path and delete whatever is left of it afterwards."""
    staging = destination.with_name(
        f".{destination.name}.biomero-materialize-{uuid4().hex}"
    )
    try:
        yield staging
    finally:
        if staging.exists():
            logger.debug(f"Removing materialization staging {staging}")
            shutil.rmtree(staging, ignore_errors=True)


def _skip_labels(image_directories: set[Path]):
    """Return a ``shutil.copytree`` ignore function that drops markers and image labels.

    Labels are copied separately from their managed sources, so the canonical
    image's own ``labels`` folder must not come along.
    """
    def ignore(directory, names):
        ignored = {CANONICAL_MARKER_NAME}.intersection(names)
        if Path(directory) in image_directories and LABELS_GROUP_NAME in names:
            ignored.add(LABELS_GROUP_NAME)
        return ignored
    return ignore


def _label_source(component, reference, collection_root, storage_roots):
    """Return ``(physical label path, managed source)`` of one label component.

    A label without its own source lives in this collection; an inherited one
    lives in the canonical store it names.
    """
    if component.source is None:
        managed_source = ManagedZarrNode(
            storage_root=reference.storage_root,
            relative_path=reference.relative_path,
            node_path=component.logical_node_path,
        )
        return node_directory(collection_root, component.logical_node_path), managed_source
    physical_root = resolve_managed_source_path(component.source, storage_roots)
    return (node_directory(physical_root, component.source.node_path),
            component.source)


def _require_label_present(physical_path: Path, description: str) -> None:
    if not physical_path.is_dir():
        raise PixelIdentityError(
            f"Managed shallow {description} is unavailable: {physical_path}"
        )


def _require_label_inventory(staging, expected_paths, description):
    """Check the staged store holds exactly the labels the collection declares."""
    discovered_paths = {
        node.node_path for node in discover_ngff_nodes(staging)
        if node.role == "label"
    }
    if discovered_paths != expected_paths:
        raise PixelIdentityError(
            f"Materialized {description}inventory differs from shallow collection"
        )


def _materialize_image(reference, destination, storage_roots, move,
                       collection_root, manifest):
    """Copy one canonical image and its labels into a conventional Zarr."""
    logger.info(f"Materializing image {reference.image_node_path} "
                f"from {collection_root} to {destination}")
    matches = [
        image for image in manifest.collection.images
        if image.node_path == reference.image_node_path
    ]
    if len(matches) != 1:
        raise PixelIdentityError(
            "Shallow reference must identify exactly one collection image"
        )
    image = matches[0]
    image_binding = manifest.image_binding(image.node_id)
    label_bindings = manifest.label_bindings_for_image(image.node_id)
    label_paths = tuple(
        binding.component.logical_node_path for binding in label_bindings
    )
    if (image_binding.source != reference.source
            or set(label_paths) != set(reference.label_node_paths)):
        raise PixelIdentityError(
            "Shallow reference no longer matches its collection manifest"
        )
    canonical_root = resolve_managed_source_path(image_binding.source, storage_roots)
    canonical_image = node_directory(canonical_root, image_binding.source.node_path)
    if not canonical_image.is_dir():
        raise PixelIdentityError(
            f"Shallow source image node is unavailable: {canonical_image}"
        )
    labels_prefix = PurePosixPath(join_node_path(image.node_path, LABELS_GROUP_NAME))

    managed_labels = []
    with _staging_directory(destination) as staging:
        shutil.copytree(canonical_image, staging, symlinks=True,
                        ignore=_skip_labels({canonical_image}))
        labels_root = staging / LABELS_GROUP_NAME
        labels_root.mkdir(parents=True, exist_ok=False)
        label_names = []
        for binding in label_bindings:
            component = binding.component
            logical = PurePosixPath(component.logical_node_path)
            try:
                label_name = logical.relative_to(labels_prefix).as_posix()
            except ValueError as exc:
                raise PixelIdentityError(
                    "Label paths must be nested below their image labels/"
                ) from exc
            label_names.append(label_name)
            physical_path, managed_source = _label_source(
                component, reference, collection_root, storage_roots)
            _require_label_present(physical_path, "label")
            target_node = join_node_path(LABELS_GROUP_NAME, label_name)
            shutil.copytree(physical_path, node_directory(staging, target_node),
                            symlinks=True)
            managed_labels.append(ZarrLabelComponent(
                logical_node_path=target_node,
                pixel_identity=component.pixel_identity.model_copy(update={
                    "node_path": target_node,
                }),
                source=managed_source,
            ))
        write_labels_group(labels_root, label_names, group_metadata(staging)[3])
        expected_paths = {
            join_node_path(LABELS_GROUP_NAME,
                           PurePosixPath(path).relative_to(labels_prefix).as_posix())
            for path in label_paths
        }
        _require_label_inventory(staging, expected_paths, "label ")
        move(staging, destination)

    logger.info(f"Materialized {destination} with {len(managed_labels)} labels")
    return MaterializedShallowResult(
        destination=destination,
        manifest=manifest,
        labels=tuple(managed_labels),
    )


def _materialize_plate(reference, destination, storage_roots, move,
                       collection_root, manifest):
    """Copy a canonical Plate and every field's labels into a conventional Zarr."""
    logger.info(f"Materializing Plate from {collection_root} to {destination}")
    try:
        expected_reference = ShallowPlateReference.from_manifest(
            manifest,
            storage_root=reference.storage_root,
            relative_path=reference.relative_path,
        )
    except ValueError as exc:
        raise PixelIdentityError(
            "Shallow Plate collection has inconsistent canonical sources"
        ) from exc
    if expected_reference != reference:
        raise PixelIdentityError(
            "Shallow Plate reference no longer matches its collection manifest"
        )

    first_source = manifest.bindings.images[0].source
    canonical_root = resolve_managed_source_path(first_source, storage_roots)
    canonical_image_directories = {
        node_directory(
            canonical_root, manifest.image_binding(image.node_id).source.node_path
        )
        for image in manifest.collection.images
    }
    for directory in canonical_image_directories:
        if not directory.is_dir():
            raise PixelIdentityError(
                f"Shallow Plate source image node is unavailable: {directory}"
            )

    managed_labels = []
    expected_label_paths = set()
    with _staging_directory(destination) as staging:
        shutil.copytree(canonical_root, staging, symlinks=True,
                        ignore=_skip_labels(canonical_image_directories))
        for image in manifest.collection.images:
            components = tuple(
                binding.component
                for binding in manifest.label_bindings_for_image(image.node_id)
            )
            labels_root = node_directory(
                staging, join_node_path(image.node_path, LABELS_GROUP_NAME))
            labels_root.mkdir()
            labels_prefix = PurePosixPath(
                join_node_path(image.node_path, LABELS_GROUP_NAME))
            label_names = []
            for component in sorted(components, key=lambda item: item.logical_node_path):
                relative = _plate_label_name(component, labels_prefix)
                label_names.append(relative.name)
                expected_label_paths.add(component.logical_node_path)
                physical_path, managed_source = _label_source(
                    component, reference, collection_root, storage_roots)
                _require_label_present(physical_path, "Plate label")
                shutil.copytree(
                    physical_path,
                    node_directory(staging, component.logical_node_path),
                    symlinks=True)
                managed_labels.append(component.model_copy(update={
                    "source": managed_source,
                }))
            write_labels_group(
                labels_root, label_names, group_metadata(labels_root.parent)[3])
        _require_label_inventory(staging, expected_label_paths, "Plate label ")
        move(staging, destination)

    logger.info(f"Materialized Plate {destination} with {len(managed_labels)} labels")
    return MaterializedShallowResult(
        destination=destination,
        manifest=manifest,
        labels=tuple(managed_labels),
    )


def _plate_label_name(component, labels_prefix: PurePosixPath) -> PurePosixPath:
    """Return a Plate label's single name below its image's labels/ group, or raise."""
    logical = PurePosixPath(component.logical_node_path)
    try:
        relative = logical.relative_to(labels_prefix)
    except ValueError as exc:
        raise PixelIdentityError(
            "Plate label paths must be nested below their image labels/"
        ) from exc
    if len(relative.parts) != 1:
        raise PixelIdentityError(
            "Plate labels must have one name below the image labels/ group"
        )
    return relative
