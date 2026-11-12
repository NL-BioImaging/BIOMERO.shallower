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
"""The prune journal: atomic cleanup order, recovery and the importer-facing helpers."""

import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST

from biomero_shallower.transaction import (
    JOURNAL_NAME, begin, commit, pending_prune_journals, prune_was_committed,
    recover, remove_rollback, restore_moved_arrays, rollback_path,
)


def make_rollback(root, *, committed, arrays=('0',)):
    """Create a rollback folder holding the journal and the arrays it moved."""
    rollback = rollback_path(root, 'test')
    rollback.mkdir()
    (rollback / JOURNAL_NAME).write_text(json.dumps(
        {'schema': 1, 'committed': committed, 'moves': list(arrays), 'attrs': {}}))
    for array in arrays:
        (rollback / array).mkdir()
        (rollback / array / 'chunk').write_bytes(b'x')
    return rollback


def interrupt_when(target_path):
    """Return a replacement for a Path method that raises KeyboardInterrupt for one path."""
    def interrupt(original):
        def method(self, *args, **kwargs):
            if self == target_path:
                raise KeyboardInterrupt
            return original(self, *args, **kwargs)
        return method
    return interrupt


def test_cleanup_deletes_the_journal_last(store):
    rollback = make_rollback(store, committed=True, arrays=('a', 'b'))
    real_rmtree = shutil.rmtree
    removed = []

    def interrupt_after_first(path, *args, **kwargs):
        if removed:
            raise KeyboardInterrupt
        removed.append(path)
        real_rmtree(path, *args, **kwargs)

    with patch('biomero_shallower.transaction.shutil.rmtree', side_effect=interrupt_after_first):
        with pytest.raises(KeyboardInterrupt):
            remove_rollback(rollback)
    assert (rollback / JOURNAL_NAME).exists()
    recover(store)
    assert not rollback.exists()


@pytest.mark.parametrize('interrupted_method', ['unlink', 'rmdir'])
def test_cleanup_interrupted_at_journal_or_folder_converges(store, interrupted_method):
    rollback = make_rollback(store, committed=True)
    target = rollback / JOURNAL_NAME if interrupted_method == 'unlink' else rollback
    original = getattr(Path, interrupted_method)
    with patch.object(Path, interrupted_method, interrupt_when(target)(original)):
        with pytest.raises(KeyboardInterrupt):
            remove_rollback(rollback)
    recover(store)
    recover(store)
    assert not rollback.exists()


def test_interrupted_recovery_of_uncommitted_prune_restores_arrays_on_rerun(store):
    rollback = make_rollback(store, committed=False)
    with patch('biomero_shallower.transaction._delete_rollback_folder', side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            recover(store)
    assert (store / '0' / 'chunk').read_bytes() == b'x'
    recover(store)
    assert (store / '0' / 'chunk').read_bytes() == b'x'
    assert not rollback.exists()


def test_recovery_refuses_to_overwrite_an_array_present_in_both_places(store):
    rollback = make_rollback(store, committed=False)
    (store / '0').mkdir()
    (store / '0' / 'chunk').write_bytes(b'newer')
    with pytest.raises(ValueError, match='Conflicting recovery paths'):
        recover(store)
    assert (store / '0' / 'chunk').read_bytes() == b'newer'
    assert (rollback / '0' / 'chunk').read_bytes() == b'x'


def test_restore_moved_arrays_rejects_an_unsafe_path(store):
    rollback = make_rollback(store, committed=False)
    with pytest.raises(Exception, match='Unsafe'):
        restore_moved_arrays(store, rollback, ['../escape'])


def test_remove_rollback_refuses_an_uncommitted_journal(store):
    rollback = make_rollback(store, committed=False)
    with pytest.raises(ValueError, match='uncommitted'):
        remove_rollback(rollback)
    assert (rollback / '0' / 'chunk').exists()


def test_folder_with_only_an_unfinished_journal_write_is_removed_as_empty(store):
    rollback = rollback_path(store, 'test')
    rollback.mkdir()
    (rollback / (JOURNAL_NAME + '.tmp')).write_text('{"schema"')
    recover(store)
    assert not rollback.exists()


def test_folder_with_other_files_and_no_journal_is_refused(store):
    rollback = rollback_path(store, 'test')
    (rollback / '0').mkdir(parents=True)
    with pytest.raises(ValueError, match='Incomplete recovery journal'):
        recover(store)


@pytest.mark.parametrize('content', ['{"schema"', '{"schema": 2}', '{"schema": 1}', '[]'])
def test_malformed_or_unknown_journal_is_rejected(store, content):
    rollback = rollback_path(store, 'test')
    rollback.mkdir()
    (rollback / JOURNAL_NAME).write_text(content)
    with pytest.raises(ValueError):
        pending_prune_journals(store)
    with pytest.raises(ValueError):
        recover(store)


def test_symlinked_rollback_folder_is_rejected(store, tmp_path):
    target = tmp_path / 'elsewhere'
    target.mkdir()
    try:
        rollback_path(store, 'test').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks are not available')
    with pytest.raises(ValueError, match='Symlink'):
        pending_prune_journals(store)


def test_begin_and_commit_round_trip_the_journal(store):
    rollback = rollback_path(store, 'test')
    rollback.mkdir()
    array, attributes = store / '0', store / '.zattrs'
    journal = begin(store, rollback, [array], {attributes: b'{}'})
    assert not journal.committed and journal.moves == ('0',)
    assert [item.committed for item in pending_prune_journals(store)] == [False]
    assert commit(journal).committed
    assert [item.committed for item in pending_prune_journals(store)] == [True]


@pytest.mark.parametrize('journal_committed, manifest_present, expected', [
    (False, True, False),
    (True, True, True),
    (None, True, True),
    (None, False, False),
])
def test_prune_was_committed_from_journal_and_manifest(
        store, journal_committed, manifest_present, expected):
    if manifest_present:
        (store / SHALLOW_COLLECTION_MANIFEST).write_text('{}')
    if journal_committed is not None:
        make_rollback(store, committed=journal_committed)
    assert prune_was_committed(store) is expected
    assert len(pending_prune_journals(store)) == (journal_committed is not None)
