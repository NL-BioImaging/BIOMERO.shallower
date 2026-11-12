"""Durable intent journal for same-filesystem moves; no retained-tree copies."""

import base64
import json
import logging
import os
from pathlib import Path
import shutil

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST

JOURNAL_NAME = "journal.json"
JOURNAL_SCHEMA = 1
# A rollback folder is a sibling of the store: ".<store name>.biomero-prune-<token>".
ROLLBACK_INFIX = ".biomero-prune-"

logger = logging.getLogger(__name__)


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


def rollback_path(root, token):
    """Return the sibling rollback folder that one prune of ``root`` journals into."""
    root = Path(root)
    return root.with_name(f".{root.name}{ROLLBACK_INFIX}{token}")


def rollback_folders(root):
    """Return every rollback folder left beside ``root`` by earlier prunes."""
    root = Path(root)
    return list(root.parent.glob(f".{root.name}{ROLLBACK_INFIX}*"))


def begin(root, rollback, directories, attrs):
    state = {
        "schema": JOURNAL_SCHEMA, "committed": False,
        "moves": [str(path.relative_to(root).as_posix()) for path in directories],
        "attrs": {path.relative_to(root).as_posix(): base64.b64encode(data).decode()
                  for path, data in attrs.items()},
    }
    write_json(rollback / JOURNAL_NAME, state)
    return state


def remove_rollback(rollback):
    """Delete a rollback folder keeping its journal until last.

    An interruption then leaves a journal that `recover` can finish.
    """
    rollback = Path(rollback)
    journal_path = rollback / JOURNAL_NAME
    logger.info(f"Removing rollback folder {rollback}")
    for child in rollback.iterdir():
        if child.name == JOURNAL_NAME:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    sync_directory(rollback)
    journal_path.unlink(missing_ok=True)
    rollback.rmdir()


def pending_prune_journals(root):
    """Return the journals of prunes of ``root`` that recovery has not yet cleaned up.

    Each journal is a dict with a boolean ``committed``. Folders without a
    journal (empty, or never started) are skipped.
    """
    journals = []
    for rollback in rollback_folders(root):
        journal_path = rollback / JOURNAL_NAME
        if journal_path.exists():
            journals.append(json.loads(journal_path.read_text(encoding="utf-8")))
    return journals


def prune_committed(root):
    """Whether ``root`` holds a shallow result that no unfinished prune still owns.

    True when the shallow manifest is present and no pending journal is
    uncommitted. A committed journal only awaits cleanup; an uncommitted one
    means the prune must be rolled back by `recover`.
    """
    root = Path(root)
    if not (root / SHALLOW_COLLECTION_MANIFEST).exists():
        return False
    return all(journal["committed"] for journal in pending_prune_journals(root))


def read_journal(rollback):
    """Return the journal of a rollback folder, or None for an empty folder.

    An empty folder can remain if the process was killed before the intent was
    saved; it is removed.  A non-empty folder without a journal is refused.
    """
    journal_path = rollback / JOURNAL_NAME
    if not journal_path.exists():
        if not list(rollback.iterdir()):
            logger.info(f"Removing empty rollback folder {rollback}")
            rollback.rmdir()
            return None
        raise ValueError("Incomplete recovery journal; manual recovery required")
    state = json.loads(journal_path.read_text(encoding="utf-8"))
    if state.get("schema") != JOURNAL_SCHEMA:
        raise ValueError("Unknown recovery journal version")
    return state


def restore_moved_arrays(root, rollback, moves):
    """Move every journaled array back from the rollback folder, last move first."""
    from .result_zarr import _safe_node_path
    for relative in reversed(moves):
        relative = _safe_node_path(relative)
        source, target = root / relative, rollback / relative
        if target.exists():
            if source.exists():
                raise ValueError("Conflicting recovery paths; refusing data loss")
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, source)


def restore_attributes(root, saved_attributes):
    """Rewrite the original node attributes saved (base64) in the journal."""
    from .result_zarr import _write_bytes, _safe_node_path
    for relative, encoded in saved_attributes.items():
        _write_bytes(root / _safe_node_path(relative), base64.b64decode(encoded))


def undo_uncommitted_prune(root, rollback, state):
    """Restore arrays and attributes, and drop the shallow files of a prune that never committed."""
    moves, saved_attributes = state["moves"], state["attrs"]
    restore_moved_arrays(root, rollback, moves)
    restore_attributes(root, saved_attributes)
    logger.info(f"Restored {len(moves)} arrays and {len(saved_attributes)} attribute files in {root}")
    for name in (SHALLOW_COLLECTION_MANIFEST, SHALLOW_OPERATION_REPORT):
        (root / name).unlink(missing_ok=True)


def recover(root):
    """Finish or undo every interrupted prune of ``root``.

    An uncommitted prune is rolled back; a committed one only has its rollback
    folder removed.  Safe to rerun after an interruption at any step.
    """
    root = Path(root)
    for rollback in rollback_folders(root):
        if rollback.is_symlink():
            raise ValueError("Symlink recovery journal")
        state = read_journal(rollback)
        if state is None:
            continue
        committed = state["committed"]
        logger.info(f"Recovering {root} from {rollback}: committed={committed}, "
                    f"moves={len(state['moves'])}")
        if not committed:
            undo_uncommitted_prune(root, rollback, state)
        remove_rollback(rollback)
        sync_directory(root.parent)
