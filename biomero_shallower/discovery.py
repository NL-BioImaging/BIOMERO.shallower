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
Find returned OME-Zarr stores and the NGFF image and label nodes inside them.

Discovery only reads metadata; it never opens pixels or changes a store.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Literal

from .ngff import (
    LABELS_GROUP_NAME, PixelIdentityError, join_node_path, node_directory, read_attrs,
    safe_node_path,
)
# Rollback folder names are owned by the prune journal; discovery must skip them
# so it never walks (or returns stores found inside) a half-pruned tree.
from .transaction import ROLLBACK_INFIX

__all__ = ["NgffNode", "discover_ngff_nodes", "find_returned_zarr_stores"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NgffNode:
    """
    One explicitly discovered image or label node in a returned store.

    Attributes:
        node_path (str): The node path relative to the store root.
        role (Literal["image", "label"]): Whether the node is an image or a label.
        parent_image_node_path (str | None): For a label, the node path of the
            image that declares it; None for an image.
    """

    node_path: str
    role: Literal["image", "label"]
    parent_image_node_path: str | None = None


def _discover_labels(root: Path, image_node_path: str) -> list[NgffNode]:
    """
    Return the label nodes an image declares in its ``labels`` group, validated.

    Args:
        root (Path): The store root.
        image_node_path (str): The node path of the image.

    Returns:
        list[NgffNode]:
            The label nodes of the image.

    Raises:
        PixelIdentityError: If the labels metadata is malformed, a label node
            is duplicated or a label node has no multiscales metadata.
    """
    image_node = node_directory(root, image_node_path)
    labels_group = image_node / LABELS_GROUP_NAME
    if not labels_group.is_dir():
        return []
    labels_attrs = read_attrs(labels_group)
    labels = labels_attrs.get(LABELS_GROUP_NAME)
    if not isinstance(labels, list):
        raise PixelIdentityError(
            f"NGFF labels metadata has no labels list: {labels_group}"
        )

    discovered = []
    seen = set()
    for label in labels:
        if not isinstance(label, str):
            raise PixelIdentityError("NGFF label names must be strings")
        label = safe_node_path(label)
        label_node_path = join_node_path(image_node_path, LABELS_GROUP_NAME, label)
        if label_node_path in seen:
            raise PixelIdentityError(
                f"Duplicate NGFF label node: {label_node_path}"
            )
        label_attrs = read_attrs(node_directory(root, label_node_path))
        if "multiscales" not in label_attrs:
            raise PixelIdentityError(
                f"NGFF label node has no multiscales metadata: {label_node_path}"
            )
        discovered.append(NgffNode(
            node_path=label_node_path,
            role="label",
            parent_image_node_path=image_node_path,
        ))
        seen.add(label_node_path)
    return discovered


def _plate_image_paths(root: Path, plate: Any) -> list[str]:
    """
    Return the image node paths of every well of a Plate, validating its
    metadata.

    Args:
        root (Path): The store root.
        plate (Any): The ``plate`` object of the root NGFF attributes.

    Returns:
        list[str]:
            The image node paths relative to ``root``.

    Raises:
        PixelIdentityError: If the plate, well or image metadata is missing or
            malformed.
    """
    wells = plate.get("wells") if isinstance(plate, dict) else None
    if not isinstance(wells, list):
        raise PixelIdentityError("NGFF plate metadata has no wells list")
    image_paths = []
    for well in wells:
        well_path = well.get("path") if isinstance(well, dict) else None
        if not isinstance(well_path, str):
            raise PixelIdentityError("NGFF plate well path is missing")
        well_path = safe_node_path(well_path)
        well_attrs = read_attrs(node_directory(root, well_path))
        well_metadata = well_attrs.get("well")
        images = (
            well_metadata.get("images")
            if isinstance(well_metadata, dict)
            else None
        )
        if not isinstance(images, list):
            raise PixelIdentityError(
                f"NGFF well metadata has no images list: {well_path}"
            )
        for image in images:
            image_path = image.get("path") if isinstance(image, dict) else None
            if not isinstance(image_path, str):
                raise PixelIdentityError(
                    f"NGFF well image path is missing: {well_path}"
                )
            image_paths.append(join_node_path(well_path, safe_node_path(image_path)))
    return image_paths


def discover_ngff_nodes(zarr_root: str | Path) -> tuple[NgffNode, ...]:
    """
    Discover NGFF image-level nodes by declared image/plate metadata.

    Args:
        zarr_root (str | Path): The root of a returned Zarr store.

    Returns:
        tuple[NgffNode, ...]:
            The image nodes, each followed by its label nodes.

    Raises:
        PixelIdentityError: If the root is neither an image nor a plate, or an
            image or label node is duplicated or invalid.
    """
    root = Path(zarr_root)
    attrs = read_attrs(root)
    if "multiscales" in attrs:
        image_paths = ["."]
    elif "plate" in attrs:
        image_paths = _plate_image_paths(root, attrs["plate"])
    else:
        raise PixelIdentityError(
            "Returned Zarr root is neither an NGFF image nor an NGFF plate"
        )

    nodes: list[NgffNode] = []
    seen_images = set()
    for image_path in image_paths:
        if image_path in seen_images:
            raise PixelIdentityError(f"Duplicate NGFF image node: {image_path}")
        image_attrs = read_attrs(node_directory(root, image_path))
        if "multiscales" not in image_attrs:
            raise PixelIdentityError(
                f"NGFF image node has no multiscales metadata: {image_path}"
            )
        nodes.append(NgffNode(node_path=image_path, role="image"))
        nodes.extend(_discover_labels(root, image_path))
        seen_images.add(image_path)
    return tuple(nodes)


def find_returned_zarr_stores(results_path: str | Path) -> tuple[Path, ...]:
    """
    Find outermost returned ``*.zarr`` stores and prune their internals.

    Args:
        results_path (str | Path): The directory to search.

    Returns:
        tuple[Path, ...]:
            The outermost ``*.zarr`` store directories found.
    """
    base = Path(results_path)
    stores = []
    for current, dirs, _files in os.walk(base):
        zarr_dirs = sorted(name for name in dirs if name.lower().endswith(".zarr"))
        stores.extend(Path(current) / name for name in zarr_dirs)
        dirs[:] = [name for name in dirs
                   if not name.lower().endswith(".zarr")
                   and ROLLBACK_INFIX not in name]
    return tuple(stores)
