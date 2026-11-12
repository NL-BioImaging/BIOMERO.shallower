"""TODO."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Literal


from .pixel_identity import (
    PixelIdentityError,
)
from .ngff import (
    read_attrs,
)
from .transaction import (
    ROLLBACK_INFIX,
)


from .ngff import join_node_path, node_directory, safe_node_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NgffNode:
    """One explicitly discovered image or label node in a returned store."""

    node_path: str
    role: Literal["image", "label"]
    parent_image_node_path: str | None = None


def _discover_labels(root: Path, image_node_path: str) -> list[NgffNode]:
    image_node = node_directory(root, image_node_path)
    labels_group = image_node / "labels"
    if not labels_group.is_dir():
        return []
    labels_attrs = read_attrs(labels_group)
    labels = labels_attrs.get("labels")
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
        label_node_path = join_node_path(image_node_path, "labels", label)
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


def discover_ngff_nodes(zarr_root: str | Path) -> tuple[NgffNode, ...]:
    """Discover NGFF image-level nodes by declared image/plate metadata."""
    root = Path(zarr_root)
    attrs = read_attrs(root)
    image_paths: list[str] = []

    if "multiscales" in attrs:
        image_paths.append(".")
    elif "plate" in attrs:
        plate = attrs["plate"]
        wells = plate.get("wells") if isinstance(plate, dict) else None
        if not isinstance(wells, list):
            raise PixelIdentityError("NGFF plate metadata has no wells list")
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
                image_paths.append(join_node_path(
                    well_path,
                    safe_node_path(image_path),
                ))
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
    """Find outermost returned ``*.zarr`` stores and prune their internals."""
    base = Path(results_path)
    stores = []
    for current, dirs, _files in os.walk(base):
        zarr_dirs = sorted(name for name in dirs if name.lower().endswith(".zarr"))
        stores.extend(Path(current) / name for name in zarr_dirs)
        dirs[:] = [name for name in dirs
                   if not name.lower().endswith(".zarr")
                   and ROLLBACK_INFIX not in name]
    return tuple(stores)
