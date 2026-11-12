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
Filesystem metadata for NGFF 0.4/v2 and 0.5/v3, without reading pixels.
"""

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .filesystem import write_json


class PixelIdentityError(RuntimeError):
    """
    An exact pixel identity or its filesystem metadata is ambiguous.
    """


LABELS_GROUP_NAME = "labels"


def read_json_object(path: str | Path, description: str) -> dict[str, Any]:
    """
    Read a JSON object file, or raise `PixelIdentityError` naming ``description``.

    Args:
        path (str | Path): The JSON file to read.
        description (str): What the file is, used in error messages.

    Returns:
        dict[str, Any]:
            The parsed JSON object.

    Raises:
        PixelIdentityError: If the file cannot be read or parsed, or is not a
            JSON object.
    """
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PixelIdentityError(f"Cannot read {description} metadata: {path}") from exc
    if not isinstance(value, dict):
        raise PixelIdentityError(f"Invalid {description} metadata: {path}")
    return value


def metadata_path(node: str | Path) -> Path:
    """
    Return a node's attribute file: ``zarr.json`` (Zarr v3) or ``.zattrs`` (v2).

    Args:
        node (str | Path): The group or array directory.

    Returns:
        Path:
            The attribute file of the node.

    Raises:
        PixelIdentityError: If both format generations are present.
    """
    node = Path(node)
    v3 = node / "zarr.json"
    if v3.exists():
        if any((node / name).exists() for name in (".zattrs", ".zgroup", ".zarray")):
            raise PixelIdentityError(f"Ambiguous v2/v3 metadata: {node}")
        return v3
    return node / ".zattrs"


def group_metadata(node: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any], int]:
    """
    Return ``(path, document, NGFF attributes, Zarr format)`` of a group node.

    Args:
        node (str | Path): The group directory.

    Returns:
        tuple[Path, dict[str, Any], dict[str, Any], int]:
            The metadata file path, its document, the NGFF attributes and the
            Zarr format version (2 or 3).

    Raises:
        PixelIdentityError: For unsupported metadata: consolidated v3 metadata
            and anything but OME-Zarr 0.5 on v3.
    """
    path = metadata_path(node)
    doc = read_json_object(path, "NGFF group")
    if path.name == ".zattrs":
        return path, doc, doc, 2
    if doc.get("zarr_format") != 3 or doc.get("node_type") != "group":
        raise PixelIdentityError(f"Expected a Zarr v3 group: {path}")
    # A cached tree could still advertise omitted arrays after normalization.
    # Keep these stores full until consolidated-metadata rewriting is supported.
    if doc.get("consolidated_metadata") is not None:
        raise PixelIdentityError(f"Consolidated Zarr v3 metadata is unsupported: {path}")
    attrs = doc.get("attributes", {})
    ome = attrs.get("ome") if isinstance(attrs, dict) else None
    if not isinstance(ome, dict) or ome.get("version") != "0.5":
        raise PixelIdentityError(f"Only OME-Zarr 0.5/v3 metadata is supported: {path}")
    return path, doc, ome, 3


def read_attrs(node: str | Path) -> dict[str, Any]:
    """
    Return a node's NGFF attributes (the ``ome`` object on v3).

    Args:
        node (str | Path): The group directory.

    Returns:
        dict[str, Any]:
            The NGFF attributes.
    """
    return group_metadata(node)[2]


def interchange_profile(node: str | Path) -> str:
    """
    Return the interchange profile name of a node's NGFF and Zarr format.

    Args:
        node (str | Path): The group directory.

    Returns:
        str:
            ``ngff-0.4-zarr-v2`` or ``ngff-0.5-zarr-v3``.
    """
    return {2: "ngff-0.4-zarr-v2", 3: "ngff-0.5-zarr-v3"}[group_metadata(node)[3]]


def encoded_attrs(node: str | Path, attrs: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """
    Replace NGFF attributes while retaining the v3 envelope and extensions.

    Args:
        node (str | Path): The group directory.
        attrs (dict[str, Any]): The new NGFF attributes.

    Returns:
        tuple[Path, dict[str, Any]]:
            The metadata file path and the full document to write there.
    """
    path, doc, _, version = group_metadata(node)
    if version == 2:
        return path, attrs
    doc["attributes"]["ome"] = attrs
    return path, doc


def new_group_metadata(
    node: str | Path, attrs: dict[str, Any], version: int
) -> dict[Path, dict[str, Any]]:
    """
    Return ``{file: content}`` that creates a group with ``attrs`` for a Zarr
    format version.

    Args:
        node (str | Path): The group directory to create.
        attrs (dict[str, Any]): The NGFF attributes of the new group.
        version (int): The Zarr format version, 2 or 3.

    Returns:
        dict[Path, dict[str, Any]]:
            A mapping from metadata file to its JSON content.

    Raises:
        PixelIdentityError: If ``version`` is not 2 or 3.
    """
    node = Path(node)
    if version == 2:
        return {node / ".zgroup": {"zarr_format": 2}, node / ".zattrs": attrs}
    if version == 3:
        return {node / "zarr.json": {
            "zarr_format": 3, "node_type": "group",
            "attributes": {"ome": {"version": "0.5", **attrs}},
        }}
    raise PixelIdentityError(f"Unsupported Zarr format: {version}")


def safe_node_path(value: str) -> str:
    """
    Return a node path that is relative and stays inside its store, or raise.

    Args:
        value (str): The node path to check.

    Returns:
        str:
            ``value`` unchanged when it is safe.

    Raises:
        PixelIdentityError: If the path is empty, contains a backslash, is
            absolute or contains "..".
    """
    if not value or "\\" in value:
        raise PixelIdentityError(f"Unsafe NGFF node path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise PixelIdentityError(f"Unsafe NGFF node path: {value!r}")
    return value


def join_node_path(*parts: str) -> str:
    """
    Join node path parts, ignoring empty parts and "." (the store root).

    Args:
        *parts (str): The node path parts to join.

    Returns:
        str:
            The joined POSIX path, or "." if there is nothing to join.
    """
    useful = [part for part in parts if part and part != "."]
    return str(PurePosixPath(*useful)) if useful else "."


def node_directory(root: Path, node_path: str) -> Path:
    """
    Return the directory of a node path inside ``root``.

    Args:
        root (Path): The store root.
        node_path (str): The node path relative to ``root``.

    Returns:
        Path:
            The node directory.
    """
    if node_path == ".":
        return root
    return root.joinpath(*PurePosixPath(node_path).parts)


def write_attrs(node: Path, attrs: dict[str, Any]) -> None:
    """
    Atomically replace a node's NGFF attributes.

    Args:
        node (Path): The group directory.
        attrs (dict[str, Any]): The new NGFF attributes.
    """
    path, value = encoded_attrs(node, attrs)
    write_json(path, value)


def write_labels_group(node: Path, names: list[str], version: int) -> None:
    """
    Write the ``labels`` group metadata listing ``names`` for a Zarr format
    version.

    Args:
        node (Path): The labels group directory.
        names (list[str]): The label names to list.
        version (int): The Zarr format version, 2 or 3.

    Raises:
        PixelIdentityError: If ``version`` is not 2 or 3.
    """
    for path, value in new_group_metadata(node, {LABELS_GROUP_NAME: names}, version).items():
        write_json(path, value)
