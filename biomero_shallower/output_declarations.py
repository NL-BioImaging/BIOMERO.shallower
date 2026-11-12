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
"""Recognize run-bound label outputs; crate contents never define a deletion list."""

import json
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import unquote, urlsplit

from rocrate.model.entity import Entity
from rocrate.rocrate import ROCrate
from rocrate.utils import as_list


def _read_crate(path: Path) -> ROCrate:
    """
    Load an RO-Crate, rejecting duplicate or ambiguous entity IDs.

    Args:
        path (Path): The ``ro-crate-metadata.json`` file.

    Returns:
        ROCrate:
            The loaded crate.

    Raises:
        ValueError: If entity IDs are duplicated or ambiguous.
    """
    metadata = json.loads(path.read_text(encoding="utf-8"))
    identifiers = [entry["@id"] for entry in metadata["@graph"]]
    crate = ROCrate(metadata)
    # Upstream silently replaces duplicate entities, including normalized IDs.
    # Use its own ID resolution to reject this ambiguous decision evidence.
    if len({crate.resolve_id(value) for value in identifiers}) != len(identifiers):
        raise ValueError("Ambiguous RO-Crate entity IDs")
    return crate


def _references(entity: Entity, key: str) -> list[Entity]:
    """
    Return the entities a property references; an unresolved reference is an error.

    Args:
        entity (Entity): The entity holding the property.
        key (str): The property name.

    Returns:
        list[Entity]:
            The referenced entities.

    Raises:
        ValueError: If any referenced value is not a resolved entity.
    """
    values = as_list(entity.get(key, []))
    if any(not isinstance(value, Entity) for value in values):
        raise ValueError(f"Unresolved RO-Crate reference: {key}")
    return values


def _local_path(value: str) -> str | None:
    """
    Return a normalized relative path, or None for URLs, absolute paths and escapes.

    Args:
        value (str): The identifier or URL to interpret.

    Returns:
        str | None:
            The normalized relative POSIX path, or None if it is not local.
    """
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = PurePosixPath(unquote(parsed.path))
    if path.is_absolute() or ".." in path.parts or "\\" in str(path):
        return None
    return path.as_posix().rstrip("/")


def _artifact_stem(value: str) -> str:
    """
    Return a file name without its Zarr or TIFF extension.

    Args:
        value (str): A path or file name.

    Returns:
        str:
            The file name with a known extension removed.
    """
    # BIOMERO conversion preserves the input basename while changing format.
    name = PurePosixPath(value).name
    for suffix in (".ome.zarr", ".ome.tiff", ".ome.tif", ".zarr", ".tiff", ".tif"):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return name


def same_input_artifact(left: str | None, right: str | None) -> bool:
    """
    Whether two artifact names are the same input in different formats.

    Args:
        left (str | None): The first artifact name.
        right (str | None): The second artifact name.

    Returns:
        bool:
            True if both are set and share the same stem.
    """
    return bool(left and right and _artifact_stem(left) == _artifact_stem(right))


def references_omitted_files(root: Path, omitted: Sequence[Path]) -> bool:
    """
    Keep unsupported crates full instead of leaving dangling file entities.

    Standard label-output declarations reference retained files. A crate that
    inventories arrays being omitted needs a richer storage transformation;
    preserving the full artifact is the conservative supported fallback.

    Args:
        root (Path): The returned store directory.
        omitted (Sequence[Path]): The paths that pruning would omit.

    Returns:
        bool:
            True if the crate references an omitted file, or cannot be read.
    """
    path = next((path for path in (root.parent / "ro-crate-metadata.json",
                                  root / "ro-crate-metadata.json") if path.is_file()), None)
    if path is None:
        return False
    try:
        crate = _read_crate(path)
        omitted_paths = [PurePosixPath(item.relative_to(path.parent).as_posix()) for item in omitted]
        for entry in crate.get_entities():
            for values in (entry.id, entry.get("contentUrl"), entry.get("url")):
                for value in as_list(values):
                    value = value.id if isinstance(value, Entity) else value
                    local = _local_path(value) if isinstance(value, str) else None
                    if local is not None and any(PurePosixPath(local).is_relative_to(item)
                                                 for item in omitted_paths):
                        return True
        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # An unreadable crate cannot establish that pruning preserves its links.
        return True


def declared_label_input(
    root: Path, manifest: Any, candidates: Sequence[Any], label_paths: Sequence[str]
) -> Any | None:
    """
    Return one bound input only for explicit, consistent label-level outputs.

    Root-level output declarations or an ambiguous input/action are insufficient.
    Other results and sidecars are outside this decision and always retained.

    Args:
        root (Path): The returned store directory.
        manifest (Any): The canonical input manifest of the run.
        candidates (Sequence[Any]): The canonical inputs that may be bound.
        label_paths (Sequence[str]): The label node paths of the store.

    Returns:
        Any | None:
            The single bound canonical input, or None if the declarations are
            absent, ambiguous or unreadable.
    """
    paths = (root.parent / "ro-crate-metadata.json", root / "ro-crate-metadata.json")
    path = next((path for path in paths if path.is_file()), None)
    if path is None:
        return None
    try:
        crate = _read_crate(path)
        has_parts = {entity.id for entity in _references(crate.root_dataset, "hasPart")}
        expected = {f"{root.name}/{label}" if path.parent == root.parent else label
                    for label in label_paths}
        matches = []
        for action in crate.get_by_type("CreateAction"):
            # A crate that supplies our execution ID must agree with this run.
            if action.get("biomero:workflowId", str(manifest.workflow_id)) != str(manifest.workflow_id):
                continue
            outputs = _references(action, "result")
            labels = [output for output in outputs if _local_path(output.id) in expected]
            if not labels:
                continue
            if any(output.id not in has_parts or not _references(output, "exampleOfWork")
                   for output in labels):
                continue
            # A root/image output is derived data, even alongside declared labels.
            if any(_local_path(output.id) in {root.name, "."} for output in outputs):
                continue
            objects = {_artifact_stem(value.id) for value in _references(action, "object")
                       if _local_path(value.id) is not None}
            bound = [item for item in candidates if item.transfer_artifact
                     and _artifact_stem(item.transfer_artifact) in objects]
            if len(bound) == 1:
                matches.append(bound[0])
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
