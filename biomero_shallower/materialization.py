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
"""
Rebuild a conventional image or Plate Zarr from a shallow reference.

The managed canonical pixels and the managed labels are copied into a sibling
staging directory, which is renamed into place only after its label inventory
matches the shallow collection. The managed sources are never modified.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import logging
from pathlib import Path, PurePosixPath
import shutil
from typing import Callable, Iterator
from uuid import uuid4

from biomero_schema.zarr import (
    ManagedZarrNode,
    ShallowManifest,
    ShallowPlateReference,
    ShallowZarrReference,
    ZarrLabelComponent,
)

from .discovery import discover_ngff_nodes
from .filesystem import resolve_move
from .ngff import (
    LABELS_GROUP_NAME, PixelIdentityError, group_metadata, join_node_path, node_directory,
    write_labels_group,
)
from .registration import read_collection_manifest, resolve_managed_source_path

__all__ = ["MaterializedShallowResult", "materialize_shallow_zarr"]

logger = logging.getLogger(__name__)

# Written by the importer into canonical stores; never copied into a materialized Zarr.
CANONICAL_MARKER_NAME = ".biomero-canonical.json"


@dataclass(frozen=True)
class MaterializedShallowResult:
    """
    A full temporary workflow input reconstructed from managed components.

    Attributes:
        destination (Path): The materialized conventional Zarr.
        manifest (ShallowManifest): The shallow collection manifest it was
            built from.
        labels (tuple[ZarrLabelComponent, ...]): The copied labels, each with
            its stable managed source.
    """

    destination: Path
    manifest: ShallowManifest
    labels: tuple[ZarrLabelComponent, ...]


def materialize_shallow_zarr(
    reference: ShallowZarrReference | ShallowPlateReference,
    destination: str | Path,
    storage_roots: dict[str, Path],
    *,
    move: Callable[[Path, Path], object] | None = None,
    replace: Callable[[Path, Path], object] | None = None,
) -> MaterializedShallowResult:
    """
    Build a conventional image or Plate Zarr from a shallow reference.

    The managed source and collections are read-only. The destination is
    assembled as a sibling staging directory and atomically renamed only after
    every declared label has been copied and its stable source recorded.

    ``move`` renames the staging directory into place (default ``os.replace``;
    tests inject failures). ``replace`` is the deprecated former name
    of ``move``, still accepted for existing callers.

    Args:
        reference (ShallowZarrReference | ShallowPlateReference): The shallow
            reference to materialize.
        destination (str | Path): Where the conventional Zarr is created. It
            must not exist yet.
        storage_roots (dict[str, Path]): The managed storage roots by name.
        move (Callable[[Path, Path], object] | None, optional): Renames the
            staging directory into place. Defaults to ``os.replace``.
        replace (Callable[[Path, Path], object] | None, optional): Deprecated
            former name of ``move``.

    Returns:
        MaterializedShallowResult:
            The destination, the manifest and the copied labels.

    Raises:
        PixelIdentityError: If the destination exists, the reference no longer
            matches its collection manifest, a source or label is unavailable
            or the label inventory differs from the collection.
        ValueError: If both ``move`` and ``replace`` are given, or a managed
            source cannot be resolved.
    """
    move = resolve_move(move, replace)
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
def _staging_directory(destination: Path) -> Iterator[Path]:
    """
    Yield a sibling staging path and delete whatever is left of it afterwards.

    Args:
        destination (Path): The final destination the staging path sits beside.

    Yields:
        Path:
            The staging path, which does not exist yet.
    """
    staging = destination.with_name(
        f".{destination.name}.biomero-materialize-{uuid4().hex}"
    )
    try:
        yield staging
    finally:
        if staging.exists():
            logger.debug(f"Removing materialization staging {staging}")
            shutil.rmtree(staging, ignore_errors=True)


def _skip_labels(image_directories: set[Path]) -> Callable[[str, list[str]], set[str]]:
    """
    Return a ``shutil.copytree`` ignore function that drops markers and image labels.

    Labels are copied separately from their managed sources, so the canonical
    image's own ``labels`` folder must not come along.

    Args:
        image_directories (set[Path]): The canonical image directories whose
            ``labels`` folder must not be copied.

    Returns:
        Callable[[str, list[str]], set[str]]:
            The ``ignore`` callable for ``shutil.copytree``.
    """
    def ignore(directory: str, names: list[str]) -> set[str]:
        """Names ``shutil.copytree`` must skip in ``directory``."""
        ignored = {CANONICAL_MARKER_NAME}.intersection(names)
        if Path(directory) in image_directories and LABELS_GROUP_NAME in names:
            ignored.add(LABELS_GROUP_NAME)
        return ignored
    return ignore


def _copy_label(
    component: ZarrLabelComponent,
    reference: ShallowZarrReference | ShallowPlateReference,
    collection_root: Path,
    storage_roots: dict[str, Path],
    staging: Path,
    target_node: str,
    unavailable_message: str,
) -> ManagedZarrNode:
    """
    Copy one label tree into the staged store; return its managed source.

    A label without its own source lives in this collection; an inherited one
    lives in the canonical store it names. ``unavailable_message`` is raised,
    with the missing path, when that label tree does not exist.

    Args:
        component (ZarrLabelComponent): The label to copy.
        reference (ShallowZarrReference | ShallowPlateReference): The shallow
            reference, which locates the collection.
        collection_root (Path): The folder holding the collection manifest.
        storage_roots (dict[str, Path]): The managed storage roots by name.
        staging (Path): The staged store to copy into.
        target_node (str): The node path of the label in the staged store.
        unavailable_message (str): The error message when the label tree does
            not exist.

    Returns:
        ManagedZarrNode:
            The stable managed source the label was copied from.

    Raises:
        PixelIdentityError: If the label tree does not exist.
    """
    if component.source is None:
        managed_source = ManagedZarrNode(
            storage_root=reference.storage_root,
            relative_path=reference.relative_path,
            node_path=component.logical_node_path,
        )
        physical_path = node_directory(collection_root, component.logical_node_path)
    else:
        managed_source = component.source
        physical_root = resolve_managed_source_path(component.source, storage_roots)
        physical_path = node_directory(physical_root, component.source.node_path)
    if not physical_path.is_dir():
        raise PixelIdentityError(f"{unavailable_message}: {physical_path}")
    shutil.copytree(physical_path, node_directory(staging, target_node), symlinks=True)
    return managed_source


def _require_label_inventory(
    staging: Path, expected_paths: set[str], mismatch_message: str
) -> None:
    """
    Check the staged store holds exactly the labels the collection declares.

    Args:
        staging (Path): The staged store.
        expected_paths (set[str]): The label node paths the collection declares.
        mismatch_message (str): The error message when the inventory differs.

    Raises:
        PixelIdentityError: If the staged labels differ from ``expected_paths``.
    """
    discovered_paths = {
        node.node_path for node in discover_ngff_nodes(staging)
        if node.role == "label"
    }
    if discovered_paths != expected_paths:
        raise PixelIdentityError(mismatch_message)


def _materialize_image(
    reference: ShallowZarrReference,
    destination: Path,
    storage_roots: dict[str, Path],
    move: Callable[[Path, Path], object],
    collection_root: Path,
    manifest: ShallowManifest,
) -> MaterializedShallowResult:
    """
    Copy one canonical image and its labels into a conventional Zarr.

    Args:
        reference (ShallowZarrReference): The shallow image reference.
        destination (Path): Where the conventional Zarr is created.
        storage_roots (dict[str, Path]): The managed storage roots by name.
        move (Callable[[Path, Path], object]): Renames the staging directory
            into place.
        collection_root (Path): The folder holding the collection manifest.
        manifest (ShallowManifest): The collection manifest.

    Returns:
        MaterializedShallowResult:
            The destination, the manifest and the copied labels.

    Raises:
        PixelIdentityError: If the reference does not identify one collection
            image, no longer matches the manifest, a source is unavailable, a
            label path is not below its image or the label inventory differs.
    """
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
            target_node = join_node_path(LABELS_GROUP_NAME, label_name)
            managed_source = _copy_label(
                component, reference, collection_root, storage_roots, staging,
                target_node, "Managed shallow label is unavailable")
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
        _require_label_inventory(
            staging, expected_paths,
            "Materialized label inventory differs from shallow collection")
        move(staging, destination)

    logger.info(f"Materialized {destination} with {len(managed_labels)} labels")
    return MaterializedShallowResult(
        destination=destination,
        manifest=manifest,
        labels=tuple(managed_labels),
    )


def _materialize_plate(
    reference: ShallowPlateReference,
    destination: Path,
    storage_roots: dict[str, Path],
    move: Callable[[Path, Path], object],
    collection_root: Path,
    manifest: ShallowManifest,
) -> MaterializedShallowResult:
    """
    Copy a canonical Plate and every field's labels into a conventional Zarr.

    Args:
        reference (ShallowPlateReference): The shallow Plate reference.
        destination (Path): Where the conventional Zarr is created.
        storage_roots (dict[str, Path]): The managed storage roots by name.
        move (Callable[[Path, Path], object]): Renames the staging directory
            into place.
        collection_root (Path): The folder holding the collection manifest.
        manifest (ShallowManifest): The collection manifest.

    Returns:
        MaterializedShallowResult:
            The destination, the manifest and the copied labels.

    Raises:
        PixelIdentityError: If the collection has inconsistent canonical
            sources, the reference no longer matches the manifest, a source or
            label is unavailable, a label path is not below its image or the
            label inventory differs.
    """
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
                managed_source = _copy_label(
                    component, reference, collection_root, storage_roots, staging,
                    component.logical_node_path,
                    "Managed shallow Plate label is unavailable")
                managed_labels.append(component.model_copy(update={
                    "source": managed_source,
                }))
            write_labels_group(
                labels_root, label_names, group_metadata(labels_root.parent)[3])
        _require_label_inventory(
            staging, expected_label_paths,
            "Materialized Plate label inventory differs from shallow collection")
        move(staging, destination)

    logger.info(f"Materialized Plate {destination} with {len(managed_labels)} labels")
    return MaterializedShallowResult(
        destination=destination,
        manifest=manifest,
        labels=tuple(managed_labels),
    )


def _plate_label_name(
    component: ZarrLabelComponent, labels_prefix: PurePosixPath
) -> PurePosixPath:
    """
    Return a Plate label's single name below its image's labels/ group, or raise.

    Args:
        component (ZarrLabelComponent): The label component.
        labels_prefix (PurePosixPath): The image's ``labels`` group path.

    Returns:
        PurePosixPath:
            The label's name relative to ``labels_prefix``.

    Raises:
        PixelIdentityError: If the label is not nested directly below the
            image's labels group.
    """
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
