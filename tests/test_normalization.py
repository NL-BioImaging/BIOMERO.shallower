"""Prune planning, staged-result validation and failure handling.

The `move=` seam of `normalize_returned_zarr` simulates a prune whose moves do
not leave the store as planned, so staged-result validation must catch it.
"""

import os
from unittest.mock import patch

import pytest

from test_result_zarr import IdentityProvider, _identity, _input, _make_image, _manifest

from biomero_shallower.evaluation import evaluate_returned_zarr
from biomero_shallower.normalization import normalize_returned_zarr
from biomero_shallower.pixel_identity import PixelIdentityError
from biomero_shallower.transaction import JOURNAL_NAME

WORKFLOW_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


def assert_store_untouched(root):
    assert (root / '0').is_dir()
    assert (root / 'labels' / 'nuclei').is_dir()
    assert (root / 'labels' / 'cells').is_dir()
    assert not (root / '.biomero-shallow.json').exists()
    assert not list(root.parent.glob('.*.biomero-prune-*'))


def test_validation_rejects_image_arrays_that_were_not_moved(tmp_path):
    root = tmp_path / 'plain.zarr'
    _make_image(root)
    decision = evaluate_returned_zarr(root, _manifest(_input(0, 'plain.zarr')),
                                      identity_provider=IdentityProvider(_identity()))
    with pytest.raises(PixelIdentityError, match='retained image dataset'):
        normalize_returned_zarr(decision, WORKFLOW_ID, move=lambda source, target: None)
    assert (root / '0').is_dir() and not (root / '.biomero-shallow.json').exists()


def test_validation_rejects_a_retained_inherited_label(inherited_label_store):
    def move_everything_but_labels(source, target):
        if source.parent.name != 'labels':
            os.replace(source, target)

    with pytest.raises(PixelIdentityError, match='retained inherited label'):
        normalize_returned_zarr(inherited_label_store.decision, WORKFLOW_ID,
                                move=move_everything_but_labels)
    assert_store_untouched(inherited_label_store.returned)


def test_validation_rejects_a_lost_retained_label(inherited_label_store):
    root = inherited_label_store.returned

    def move_and_lose_a_label(source, target):
        os.replace(source, target)
        if (root / 'labels' / 'cells').exists():
            (root / 'labels' / 'cells').rename(root / 'labels' / 'lost')

    with pytest.raises(PixelIdentityError, match='lost label node'):
        normalize_returned_zarr(inherited_label_store.decision, WORKFLOW_ID,
                                move=move_and_lose_a_label)
    assert (root / '0').is_dir()
    assert (root / 'labels' / 'lost').is_dir()  # the test's own rename is not undone


def test_failing_begin_does_not_mask_the_original_error(inherited_label_store):
    def write_partial_journal_then_fail(root, rollback, directories, attrs):
        (rollback / (JOURNAL_NAME + '.tmp')).write_text('{"schema"')
        raise OSError('disk full')

    with patch('biomero_shallower.normalization.begin', side_effect=write_partial_journal_then_fail):
        with pytest.raises(OSError, match='disk full'):
            normalize_returned_zarr(inherited_label_store.decision, WORKFLOW_ID)
    assert_store_untouched(inherited_label_store.returned)


def test_a_failing_rollback_is_logged_and_the_prune_error_propagates(inherited_label_store, caplog):
    def fail_to_move(source, target):
        raise OSError('first')

    with patch('biomero_shallower.normalization.recover', side_effect=ValueError('second')):
        with pytest.raises(OSError, match='first'):
            normalize_returned_zarr(inherited_label_store.decision, WORKFLOW_ID, move=fail_to_move)
    assert 'Rollback of' in caplog.text


def test_deprecated_replace_keyword_still_moves(inherited_label_store):
    moves = []

    def record_and_move(source, target):
        moves.append(source)
        os.replace(source, target)

    normalize_returned_zarr(inherited_label_store.decision, WORKFLOW_ID, replace=record_and_move)
    assert moves


def test_move_and_replace_together_are_rejected(inherited_label_store):
    with pytest.raises(TypeError, match='not both'):
        normalize_returned_zarr(inherited_label_store.decision, WORKFLOW_ID,
                                move=os.replace, replace=os.replace)
