"""Prune planning, staged-result validation and failure handling."""

from unittest.mock import patch

import pytest

from biomero_schema.zarr import ManagedZarrNode, ZarrLabelComponent
from test_result_zarr import (
    IdentityProvider, NodeIdentityProvider, _identity, _input, _make_image, _manifest,
)

from biomero_shallower.evaluation import evaluate_returned_zarr
from biomero_shallower.normalization import normalize_returned_zarr
from biomero_shallower.pixel_identity import PixelIdentityError
from biomero_shallower.transaction import JOURNAL_NAME

WORKFLOW_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


@pytest.fixture
def eligible_store(tmp_path):
    """A returned store with an inherited label (nuclei) and a new one (cells)."""
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('nuclei', 'cells'))
    nuclei = _identity('ISCC:INUCLEI', 'labels/nuclei', 'label')
    inherited = ZarrLabelComponent(
        logical_node_path='labels/nuclei', pixel_identity=nuclei,
        source=ManagedZarrNode(storage_root='group-0-data',
                               relative_path='.analyzed/first/result.zarr',
                               node_path='labels/nuclei'))
    provider = NodeIdentityProvider({
        '.': _identity(), 'labels/nuclei': nuclei,
        'labels/cells': _identity('ISCC:ICELLS', 'labels/cells', 'label'),
    })
    decision = evaluate_returned_zarr(
        root, _manifest(_input(0, 'result.zarr', labels=(inherited,))),
        identity_provider=provider)
    assert decision.eligible
    return root, decision


def assert_store_untouched(root):
    assert (root / '0').is_dir()
    assert (root / 'labels' / 'nuclei').is_dir()
    assert not (root / '.biomero-shallow.json').exists()
    assert not list(root.parent.glob('.*.biomero-prune-*'))


def test_validation_rejects_image_arrays_that_were_not_moved(tmp_path):
    root = tmp_path / 'plain.zarr'
    _make_image(root)
    decision = evaluate_returned_zarr(root, _manifest(_input(0, 'plain.zarr')),
                                      identity_provider=IdentityProvider(_identity()))
    with patch('biomero_shallower.normalization._move_arrays_to_rollback'):
        with pytest.raises(PixelIdentityError, match='retained image dataset'):
            normalize_returned_zarr(decision, WORKFLOW_ID)
    assert (root / '0').is_dir() and not (root / '.biomero-shallow.json').exists()


def test_validation_rejects_a_retained_inherited_label(eligible_store):
    root, decision = eligible_store

    def move_only_image_arrays(root_, directories):
        return [directory for directory in directories if 'labels' not in directory.parts]

    with patch('biomero_shallower.normalization._top_level_directories',
               side_effect=move_only_image_arrays):
        with pytest.raises(PixelIdentityError, match='retained inherited label'):
            normalize_returned_zarr(decision, WORKFLOW_ID)
    assert_store_untouched(root)


def test_validation_rejects_a_lost_retained_label(eligible_store):
    root, decision = eligible_store
    from biomero_shallower import normalization
    real_move = normalization._move_arrays_to_rollback

    def move_and_lose_label(root_, rollback, directories, move):
        real_move(root_, rollback, directories, move)
        (root_ / 'labels' / 'cells').rename(root_ / 'labels' / 'lost')

    with patch('biomero_shallower.normalization._move_arrays_to_rollback',
               side_effect=move_and_lose_label):
        with pytest.raises(PixelIdentityError, match='lost label node'):
            normalize_returned_zarr(decision, WORKFLOW_ID)
    assert (root / '0').is_dir()
    assert (root / 'labels' / 'lost').is_dir() or (root / 'labels' / 'cells').is_dir()


def test_failing_begin_does_not_mask_the_original_error(eligible_store):
    root, decision = eligible_store

    def write_partial_journal_then_fail(root_, rollback, directories, attrs):
        (rollback / (JOURNAL_NAME + '.tmp')).write_text('{"schema"')
        raise OSError('disk full')

    with patch('biomero_shallower.normalization.begin', side_effect=write_partial_journal_then_fail):
        with pytest.raises(OSError, match='disk full'):
            normalize_returned_zarr(decision, WORKFLOW_ID)
    assert_store_untouched(root)


def test_a_failing_rollback_is_logged_and_the_prune_error_propagates(eligible_store, caplog):
    root, decision = eligible_store
    with patch('biomero_shallower.normalization._begin_prune', side_effect=OSError('first')), \
            patch('biomero_shallower.normalization.recover', side_effect=ValueError('second')):
        with pytest.raises(OSError, match='first'):
            normalize_returned_zarr(decision, WORKFLOW_ID)
    assert 'Rollback of' in caplog.text


def test_deprecated_replace_keyword_still_moves(eligible_store):
    root, decision = eligible_store
    moves = []

    def record_and_move(source, target):
        moves.append(source)
        import os
        os.replace(source, target)

    normalize_returned_zarr(decision, WORKFLOW_ID, replace=record_and_move)
    assert moves
