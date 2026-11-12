"""Finding returned stores."""

from biomero_shallower.discovery import find_returned_zarr_stores
from biomero_shallower.transaction import rollback_path


def test_stores_are_outermost_and_rollback_folders_are_not_searched(tmp_path):
    outer = tmp_path / 'results' / 'a.zarr'
    (outer / 'nested.zarr').mkdir(parents=True)
    rollback = rollback_path(tmp_path / 'results' / 'b.zarr', 'token')
    (rollback / 'moved' / 'inner.zarr').mkdir(parents=True)

    assert find_returned_zarr_stores(tmp_path) == (outer,)
