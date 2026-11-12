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
Durable file writes shared by the prune journal and the store metadata.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


def sync_directory(path: str | Path) -> None:
    """
    Flush a directory entry to disk (a no-op on Windows, which cannot open
    directories).

    Args:
        path (str | Path): The directory to flush.
    """
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def resolve_move(
    move: Callable[..., Any] | None = None,
    replace: Callable[..., Any] | None = None,
) -> Callable[..., Any]:
    """
    Return the rename function: ``move``, else the deprecated ``replace``,
    else ``os.replace``.

    ``replace`` is the former name of ``move``; passing both is an error.
    ``os.replace`` is looked up at call time so tests can substitute it.

    Args:
        move (Callable, optional): The rename function to use.
        replace (Callable, optional): Deprecated former name of ``move``.

    Returns:
        Callable:
            The rename function to call.

    Raises:
        TypeError: If both ``move`` and ``replace`` are passed.
    """
    if move is not None and replace is not None:
        raise TypeError("Pass move, or the deprecated replace, not both")
    return move or replace or os.replace


def temporary_path(path: str | Path) -> Path:
    """
    Return the sibling name an atomic write of ``path`` stages its content in.

    Args:
        path (str | Path): The file that will be written atomically.

    Returns:
        Path:
            The sibling staging path (``path`` with a ``.tmp`` suffix added).
    """
    path = Path(path)
    return path.with_name(path.name + ".tmp")


def write_bytes(path: str | Path, value: bytes) -> None:
    """
    Atomically replace ``path`` with ``value``: stage, fsync, rename, sync the
    directory.

    Args:
        path (str | Path): The file to replace.
        value (bytes): The new content.
    """
    path = Path(path)
    temporary = temporary_path(path)
    with temporary.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def write_json(path: str | Path, value: Any) -> None:
    """
    Atomically replace ``path`` with ``value`` as sorted, indented JSON.

    Args:
        path (str | Path): The file to replace.
        value (Any): The JSON-serialisable document to write.
    """
    write_bytes(path, json.dumps(value, indent=2, sort_keys=True).encode("utf-8"))
