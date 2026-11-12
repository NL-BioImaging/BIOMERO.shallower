import json
import logging

from test_result_zarr import _make_image, _manifest, _input, _identity, IdentityProvider
from biomero_shallower.operations import normalize
from biomero_shallower.result_zarr import evaluate_returned_zarr
from biomero_shallower.transaction import recover


def test_decision_and_reason_are_logged(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    _make_image(root)
    with caplog.at_level(logging.DEBUG, logger='biomero_shallower'):
        evaluate_returned_zarr(root, None)
    assert 'outcome=keep-full, reason=no-canonical-input-snapshot' in caplog.text


def test_normalize_logs_prune_commit_and_cleanup(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    _make_image(root, labels=('cells',))
    with caplog.at_level(logging.INFO, logger='biomero_shallower'):
        normalize(root, _manifest(_input(0)), identity_provider=IdentityProvider(_identity()))
    for expected in ('Pruning', 'Committed prune', 'Removing rollback folder',
                     'result=normalized'):
        assert expected in caplog.text
    assert str(root) in caplog.text


def test_recovery_is_logged(tmp_path, caplog):
    root = tmp_path / 'result.zarr'
    root.mkdir()
    rollback = tmp_path / '.result.zarr.biomero-prune-test'
    rollback.mkdir()
    (rollback / 'journal.json').write_text(json.dumps(
        {'schema': 1, 'committed': True, 'moves': [], 'attrs': {}}))
    with caplog.at_level(logging.INFO, logger='biomero_shallower'):
        recover(root)
    assert 'Recovering' in caplog.text and 'committed=True' in caplog.text
