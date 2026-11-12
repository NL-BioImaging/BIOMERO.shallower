"""Shared test setup."""

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time

import pytest

from biomero_schema.zarr import ManagedZarrNode, ZarrLabelComponent
from test_result_zarr import NodeIdentityProvider, _identity, _input, _make_image, _manifest

from biomero_shallower.evaluation import evaluate_returned_zarr


@pytest.fixture(autouse=True)
def retry_windows_replace(monkeypatch):
    """Retry ``os.replace`` when Windows refuses a rename.

    Windows denies renaming a file or directory that antivirus or the search
    indexer briefly holds open, which fails unrelated tests at random. This is
    a test-environment artefact, so the retry lives here and never in
    production code.
    """
    if sys.platform != "win32":
        return
    real_replace = os.replace

    def replace_with_retry(source, target, *args, **kwargs):
        for attempt in range(5):
            try:
                return real_replace(source, target, *args, **kwargs)
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)

    monkeypatch.setattr(os, "replace", replace_with_retry)


@pytest.fixture
def store(tmp_path):
    """An empty store directory."""
    root = tmp_path / 'result.zarr'
    root.mkdir()
    return root


@dataclass
class InheritedLabelStore:
    """A returned store whose decision is eligible, with an inherited and a new label."""

    import_root: Path
    returned: Path
    roots: dict
    decision: object


@pytest.fixture
def inherited_label_store(tmp_path):
    """Returned store ``results/result.zarr`` under an import mount, with labels
    ``nuclei`` (inherited from a prior analysis) and ``cells`` (new)."""
    import_root = tmp_path / 'data'
    prior = import_root / 'Project A/.analyzed/first/result.zarr'
    returned = import_root / 'results/result.zarr'
    _make_image(import_root / 'Project A/.processed/Image-1.ome.zarr')
    _make_image(prior, labels=('nuclei',))
    _make_image(returned, labels=('nuclei', 'cells'))
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
        returned, _manifest(_input(0, 'result.zarr', labels=(inherited,))),
        identity_provider=provider)
    assert decision.eligible
    roots = {'import-mount-data': import_root, 'group-0-data': import_root / 'Project A'}
    return InheritedLabelStore(import_root, returned, roots, decision)
