"""Durable intent journal for same-filesystem moves; no retained-tree copies.

A prune moves duplicate arrays into a sibling rollback folder and records its
intent in ``journal.json`` there. Cleanup deletes the journal last, so an
interrupted prune always leaves a journal that `recover` can finish or undo.
"""

import base64
from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
import shutil

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST

# `sync_directory` and `write_json` moved to `filesystem`; they stay importable
# from here (see `__all__`) because BIOMERO.importer imports them from this module.
from .filesystem import sync_directory, temporary_path, write_bytes, write_json
from .ngff import safe_node_path

logger = logging.getLogger(__name__)

JOURNAL_NAME = "journal.json"
JOURNAL_SCHEMA = 1
# A rollback folder is a sibling of the store: ".<store name>.biomero-prune-<token>".
ROLLBACK_INFIX = ".biomero-prune-"
# Left behind when a journal write is interrupted before its rename.
JOURNAL_TEMPORARY_NAME = temporary_path(JOURNAL_NAME).name

__all__ = [
    "JOURNAL_NAME",
    "PruneJournal",
    "begin",
    "commit",
    "pending_prune_journals",
    "prune_was_committed",
    "recover",
    "remove_rollback",
    "rollback_folders",
    "rollback_path",
    "sync_directory",
    "write_json",
]


@dataclass(frozen=True)
class PruneJournal:
    """The recorded intent of one prune: arrays moved, attributes saved, commit state."""

    committed: bool
    moves: tuple[str, ...]
    attrs: dict[str, str]
    rollback: Path

    def to_dict(self) -> dict:
        """Return the ``journal.json`` document."""
        return {"schema": JOURNAL_SCHEMA, "committed": self.committed,
                "moves": list(self.moves), "attrs": self.attrs}

    @classmethod
    def from_dict(cls, document: dict, rollback: Path) -> "PruneJournal":
        """Parse a ``journal.json`` document, rejecting unknown or malformed ones."""
        try:
            if document.get("schema") != JOURNAL_SCHEMA:
                raise ValueError("Unknown recovery journal version")
            return cls(committed=bool(document["committed"]),
                       moves=tuple(document["moves"]),
                       attrs=dict(document["attrs"]), rollback=rollback)
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError(f"Malformed recovery journal in {rollback}") from exc


def rollback_path(root, token):
    """Return the sibling rollback folder that one prune of ``root`` journals into."""
    root = Path(root)
    return root.with_name(f".{root.name}{ROLLBACK_INFIX}{token}")


def rollback_folders(root):
    """Return every rollback folder left beside ``root`` by earlier prunes."""
    root = Path(root)
    return list(root.parent.glob(f".{root.name}{ROLLBACK_INFIX}*"))


def begin(root, rollback, directories, attrs):
    """Record the intent to move ``directories`` out of ``root`` and return the journal.

    ``attrs`` maps each metadata file to its original bytes, saved (base64) so
    a rollback can restore them. The journal starts uncommitted.
    """
    journal = PruneJournal(
        committed=False,
        moves=tuple(path.relative_to(root).as_posix() for path in directories),
        attrs={path.relative_to(root).as_posix(): base64.b64encode(data).decode()
               for path, data in attrs.items()},
        rollback=Path(rollback),
    )
    write_json(journal.rollback / JOURNAL_NAME, journal.to_dict())
    return journal


def commit(journal):
    """Mark the prune committed on disk and return the committed journal."""
    committed = replace(journal, committed=True)
    write_json(committed.rollback / JOURNAL_NAME, committed.to_dict())
    return committed


def load_journal(rollback):
    """Return the journal in a rollback folder, or None if it has none.

    Raises ValueError for a symlinked folder, an unknown schema or a malformed journal.
    """
    if rollback.is_symlink():
        raise ValueError("Symlink recovery journal")
    journal_path = rollback / JOURNAL_NAME
    if not journal_path.exists():
        return None
    try:
        document = json.loads(journal_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed recovery journal in {rollback}") from exc
    return PruneJournal.from_dict(document, rollback)


def pending_prune_journals(root):
    """Return the journals of prunes of ``root`` that are not yet cleaned up.

    Folders without a journal are skipped; this never modifies anything.
    """
    journals = (load_journal(rollback) for rollback in rollback_folders(root))
    return [journal for journal in journals if journal is not None]


def prune_was_committed(root):
    """Whether a prune of ``root`` committed: the shallow manifest exists and no pending
    journal is uncommitted.

    Committed journals only await cleanup. An uncommitted journal means the
    prune is unfinished and `recover` will roll it back, so this is False.
    """
    root = Path(root)
    if not (root / SHALLOW_COLLECTION_MANIFEST).exists():
        return False
    return all(journal.committed for journal in pending_prune_journals(root))


def _delete_rollback_folder(rollback):
    """Delete a rollback folder, keeping its journal until last.

    An interruption then leaves a journal that `recover` can finish.
    """
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


def remove_rollback(rollback):
    """Delete the rollback folder of a committed prune, journal last.

    Refuses a folder whose journal is uncommitted: its arrays may be the only
    copy and must go back through `recover`.
    """
    rollback = Path(rollback)
    journal = load_journal(rollback)
    if journal is not None and not journal.committed:
        raise ValueError(f"Refusing to remove the rollback folder of an uncommitted prune: {rollback}")
    _delete_rollback_folder(rollback)


def _read_journal_discarding_empty_folder(rollback):
    """Return the journal of a rollback folder; delete the folder and return None if it is empty.

    A folder can be empty, or hold only an unfinished journal write, if the
    process was killed before the intent was saved; it is removed. A non-empty
    folder without a journal is refused.
    """
    journal = load_journal(rollback)
    if journal is not None:
        return journal
    children = list(rollback.iterdir())
    if all(child.name == JOURNAL_TEMPORARY_NAME for child in children):
        logger.info(f"Removing empty rollback folder {rollback}")
        for child in children:
            child.unlink()
        rollback.rmdir()
        return None
    raise ValueError("Incomplete recovery journal; manual recovery required")


def restore_moved_arrays(root, rollback, moves):
    """Move every journaled array back from the rollback folder, last move first."""
    for relative in reversed(moves):
        relative = safe_node_path(relative)
        source, target = root / relative, rollback / relative
        if target.exists():
            if source.exists():
                raise ValueError("Conflicting recovery paths; refusing data loss")
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, source)


def restore_attributes(root, saved_attributes):
    """Rewrite the original node attributes saved (base64) in the journal."""
    for relative, encoded in saved_attributes.items():
        write_bytes(root / safe_node_path(relative), base64.b64decode(encoded))


def undo_uncommitted_prune(root, journal):
    """Restore arrays and attributes, and drop the shallow files of a prune that never committed."""
    restore_moved_arrays(root, journal.rollback, journal.moves)
    restore_attributes(root, journal.attrs)
    logger.info(f"Restored {len(journal.moves)} arrays and {len(journal.attrs)} "
                f"attribute files in {root}")
    for name in (SHALLOW_COLLECTION_MANIFEST, SHALLOW_OPERATION_REPORT):
        (root / name).unlink(missing_ok=True)


def recover(root):
    """Finish or undo every interrupted prune of ``root``.

    An uncommitted prune is rolled back; a committed one only has its rollback
    folder removed. Safe to rerun after an interruption at any step.
    """
    root = Path(root)
    for rollback in rollback_folders(root):
        journal = _read_journal_discarding_empty_folder(rollback)
        if journal is None:
            continue
        logger.info(f"Recovering {root} from {rollback}: committed={journal.committed}, "
                    f"moves={len(journal.moves)}")
        if not journal.committed:
            undo_uncommitted_prune(root, journal)
        # The arrays are back in the store (or the prune committed): the folder is safe to delete.
        _delete_rollback_folder(rollback)
        sync_directory(root.parent)
