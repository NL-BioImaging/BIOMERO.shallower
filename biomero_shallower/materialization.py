"""TODO."""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
from uuid import uuid4

from biomero_schema.zarr import (
    ManagedZarrNode,
    SHALLOW_COLLECTION_MANIFEST,
    ShallowManifest,
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrLabelComponent,
)

from .pixel_identity import (
    PixelIdentityError,
)
from .ngff import (
    group_metadata,
)


from .ngff import join_node_path, node_directory, write_labels_group
from .discovery import discover_ngff_nodes
from .registration import resolve_managed_source_path

logger = logging.getLogger(__name__)


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
    replace=os.replace,
) -> MaterializedShallowResult:
    """Build a conventional image or Plate Zarr from a shallow reference.

    The managed source and collections are read-only. The destination is
    assembled as a sibling staging directory and atomically renamed only after
    every declared label has been copied and its stable source recorded.
    """
    if isinstance(reference, ShallowPlateReference):
        return _materialize_shallow_plate_zarr(
            reference,
            destination,
            storage_roots,
            replace=replace,
        )

    destination = Path(destination)
    if destination.exists():
        raise PixelIdentityError(
            f"Shallow materialization destination already exists: {destination}"
        )
    collection_root = resolve_managed_source_path(reference, storage_roots)
    logger.info(f"Materializing image {reference.image_node_path} "
                f"from {collection_root} to {destination}")
    manifest_path = collection_root / SHALLOW_COLLECTION_MANIFEST
    try:
        manifest = ShallowManifest.from_dict(json.loads(
            manifest_path.read_text(encoding="utf-8")
        ))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PixelIdentityError(
            f"Invalid shallow collection manifest: {manifest_path}"
        ) from exc
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
    canonical_root = resolve_managed_source_path(
        image_binding.source, storage_roots
    )
    canonical_image = node_directory(
        canonical_root, image_binding.source.node_path
    )
    if not canonical_image.is_dir():
        raise PixelIdentityError(
            f"Shallow source image node is unavailable: {canonical_image}"
        )
    components = tuple(binding.component for binding in label_bindings)

    token = uuid4().hex
    staging = destination.with_name(
        f".{destination.name}.biomero-materialize-{token}"
    )
    source_labels = canonical_image / "labels"

    def ignore(directory, names):
        current = Path(directory)
        ignored = {".biomero-canonical.json"}.intersection(names)
        if current == canonical_image and source_labels.name in names:
            ignored.add(source_labels.name)
        return ignored

    managed_labels = []
    try:
        shutil.copytree(canonical_image, staging, symlinks=True, ignore=ignore)
        labels_root = staging / "labels"
        labels_root.mkdir(parents=True, exist_ok=False)
        label_names = []
        for component in components:
            prefix = PurePosixPath(
                join_node_path(image.node_path, "labels")
            )
            logical = PurePosixPath(component.logical_node_path)
            try:
                label_name = logical.relative_to(prefix).as_posix()
            except ValueError as exc:
                raise PixelIdentityError(
                    "Label paths must be nested below their image labels/"
                ) from exc
            label_names.append(label_name)
            if component.source is None:
                physical_root = collection_root
                physical_node = component.logical_node_path
                managed_source = ManagedZarrNode(
                    storage_root=reference.storage_root,
                    relative_path=reference.relative_path,
                    node_path=component.logical_node_path,
                )
            else:
                physical_root = resolve_managed_source_path(
                    component.source,
                    storage_roots,
                )
                physical_node = component.source.node_path
                managed_source = component.source
            physical_path = node_directory(physical_root, physical_node)
            if not physical_path.is_dir():
                raise PixelIdentityError(
                    f"Managed shallow label is unavailable: {physical_path}"
                )
            target_node = join_node_path("labels", label_name)
            target = node_directory(staging, target_node)
            shutil.copytree(physical_path, target, symlinks=True)
            managed_labels.append(ZarrLabelComponent(
                logical_node_path=target_node,
                pixel_identity=component.pixel_identity.model_copy(update={
                    "node_path": target_node,
                }),
                source=managed_source,
            ))
        write_labels_group(labels_root, label_names, group_metadata(staging)[3])
        discovered_paths = {
            node.node_path for node in discover_ngff_nodes(staging)
            if node.role == "label"
        }
        expected_paths = {
            join_node_path(
                "labels",
                PurePosixPath(path).relative_to(prefix).as_posix(),
            )
            for path in label_paths
        }
        if discovered_paths != expected_paths:
            raise PixelIdentityError(
                "Materialized label inventory differs from shallow collection"
            )
        replace(staging, destination)
    finally:
        if staging.exists():
            logger.debug(f"Removing materialization staging {staging}")
            shutil.rmtree(staging, ignore_errors=True)

    logger.info(f"Materialized {destination} with {len(managed_labels)} labels")
    return MaterializedShallowResult(
        destination=destination,
        manifest=manifest,
        labels=tuple(managed_labels),
    )


def _materialize_shallow_plate_zarr(
    reference: ShallowPlateReference,
    destination: str | Path,
    storage_roots: dict[str, Path],
    *,
    replace=os.replace,
) -> MaterializedShallowResult:
    """Reconstruct one conventional Plate from its canonical pixels and labels."""
    destination = Path(destination)
    if destination.exists():
        raise PixelIdentityError(
            f"Shallow materialization destination already exists: {destination}"
        )
    collection_root = resolve_managed_source_path(reference, storage_roots)
    logger.info(f"Materializing Plate from {collection_root} to {destination}")
    manifest_path = collection_root / SHALLOW_COLLECTION_MANIFEST
    try:
        manifest = ShallowManifest.from_dict(json.loads(
            manifest_path.read_text(encoding="utf-8")
        ))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PixelIdentityError(
            f"Invalid shallow collection manifest: {manifest_path}"
        ) from exc
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

    token = uuid4().hex
    staging = destination.with_name(
        f".{destination.name}.biomero-materialize-{token}"
    )
    def ignore(directory, names):
        current = Path(directory)
        ignored = {".biomero-canonical.json"}.intersection(names)
        if current in canonical_image_directories and "labels" in names:
            ignored.add("labels")
        return ignored

    managed_labels = []
    expected_label_paths = set()
    try:
        shutil.copytree(canonical_root, staging, symlinks=True, ignore=ignore)
        for image in manifest.collection.images:
            components = tuple(
                binding.component
                for binding in manifest.label_bindings_for_image(image.node_id)
            )

            labels_root = node_directory(
                staging,
                join_node_path(image.node_path, "labels"),
            )
            labels_root.mkdir()
            label_names = []
            prefix = PurePosixPath(
                join_node_path(image.node_path, "labels")
            )
            for component in sorted(
                components, key=lambda item: item.logical_node_path
            ):
                logical = PurePosixPath(component.logical_node_path)
                try:
                    relative = logical.relative_to(prefix)
                except ValueError as exc:
                    raise PixelIdentityError(
                        "Plate label paths must be nested below their image labels/"
                    ) from exc
                if len(relative.parts) != 1:
                    raise PixelIdentityError(
                        "Plate labels must have one name below the image labels/ group"
                    )
                label_names.append(relative.name)
                expected_label_paths.add(component.logical_node_path)
                if component.source is None:
                    physical_root = collection_root
                    physical_node = component.logical_node_path
                    managed_source = ManagedZarrNode(
                        storage_root=reference.storage_root,
                        relative_path=reference.relative_path,
                        node_path=component.logical_node_path,
                    )
                else:
                    physical_root = resolve_managed_source_path(
                        component.source,
                        storage_roots,
                    )
                    physical_node = component.source.node_path
                    managed_source = component.source
                physical_path = node_directory(physical_root, physical_node)
                if not physical_path.is_dir():
                    raise PixelIdentityError(
                        f"Managed shallow Plate label is unavailable: {physical_path}"
                    )
                target = node_directory(staging, component.logical_node_path)
                shutil.copytree(physical_path, target, symlinks=True)
                managed_labels.append(component.model_copy(update={
                    "source": managed_source,
                }))
            write_labels_group(
                labels_root, label_names, group_metadata(labels_root.parent)[3]
            )

        discovered_label_paths = {
            node.node_path for node in discover_ngff_nodes(staging)
            if node.role == "label"
        }
        if discovered_label_paths != expected_label_paths:
            raise PixelIdentityError(
                "Materialized Plate label inventory differs from shallow collection"
            )
        replace(staging, destination)
    finally:
        if staging.exists():
            logger.debug(f"Removing materialization staging {staging}")
            shutil.rmtree(staging, ignore_errors=True)

    logger.info(f"Materialized Plate {destination} with {len(managed_labels)} labels")
    return MaterializedShallowResult(
        destination=destination,
        manifest=manifest,
        labels=tuple(managed_labels),
    )
