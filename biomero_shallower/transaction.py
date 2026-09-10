"""Durable intent journal for same-filesystem moves; no retained-tree copies."""

import base64
import json
import os
from pathlib import Path
import shutil


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def begin(root, rollback, directories, attrs):
    state = {
        "schema": 1, "committed": False,
        "moves": [str(path.relative_to(root).as_posix()) for path in directories],
        "attrs": {path.relative_to(root).as_posix(): base64.b64encode(data).decode()
                  for path, data in attrs.items()},
    }
    write_json(rollback / "journal.json", state)
    return state


def recover(root):
    from .result_zarr import _write_bytes, _safe_node_path
    root = Path(root)
    for rollback in root.parent.glob(f".{root.name}.biomero-prune-*"):
        if rollback.is_symlink():
            raise ValueError("Symlink recovery journal")
        journal = rollback / "journal.json"
        if not journal.exists():
            # An empty directory can remain if killed before intent was saved.
            if not list(rollback.iterdir()):
                rollback.rmdir()
                continue
            raise ValueError("Incomplete recovery journal; manual recovery required")
        state = json.loads(journal.read_text(encoding="utf-8"))
        if state.get("schema") != 1:
            raise ValueError("Unknown recovery journal version")
        if not state["committed"]:
            for relative in reversed(state["moves"]):
                relative = _safe_node_path(relative)
                source, target = root / relative, rollback / relative
                if target.exists():
                    if source.exists():
                        raise ValueError("Conflicting recovery paths; refusing data loss")
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, source)
            for relative, data in state["attrs"].items():
                _write_bytes(root / _safe_node_path(relative), base64.b64decode(data))
            for name in (".biomero-shallow.json", ".biomero-shallow-report.json"):
                (root / name).unlink(missing_ok=True)
        shutil.rmtree(rollback)
        sync_directory(root.parent)
