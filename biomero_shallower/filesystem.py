"""Durable file writes shared by the prune journal and the store metadata."""

import json
import os
from pathlib import Path


def sync_directory(path):
    """Flush a directory entry to disk (a no-op on Windows, which cannot open directories)."""
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def resolve_move(move=None, replace=None):
    """Return the rename function: ``move``, else the deprecated ``replace``, else ``os.replace``.

    ``replace`` is the former name of ``move``; passing both is an error.
    ``os.replace`` is looked up at call time so tests can substitute it.
    """
    if move is not None and replace is not None:
        raise TypeError("Pass move, or the deprecated replace, not both")
    return move or replace or os.replace


def temporary_path(path):
    """Return the sibling name an atomic write of ``path`` stages its content in."""
    path = Path(path)
    return path.with_name(path.name + ".tmp")


def write_bytes(path, value):
    """Atomically replace ``path`` with ``value``: stage, fsync, rename, sync the directory."""
    path = Path(path)
    temporary = temporary_path(path)
    with temporary.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def write_json(path, value):
    """Atomically replace ``path`` with ``value`` as sorted, indented JSON."""
    write_bytes(path, json.dumps(value, indent=2, sort_keys=True).encode("utf-8"))
