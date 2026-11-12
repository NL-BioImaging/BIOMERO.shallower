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
"""Materialization checks that its label inventory matches the shallow collection."""

from unittest.mock import patch

import pytest

from biomero_shallower import materialization
from biomero_shallower.materialization import materialize_shallow_zarr
from biomero_shallower.normalization import normalize_returned_zarr
from biomero_shallower.pixel_identity import PixelIdentityError
from biomero_shallower.registration import resolve_shallow_registration

WORKFLOW_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'


def test_materialization_rejects_a_label_inventory_that_differs(inherited_label_store, tmp_path):
    store = inherited_label_store
    normalize_returned_zarr(store.decision, WORKFLOW_ID)
    registration = resolve_shallow_registration(
        store.returned / 'labels/cells', storage_roots=store.roots,
        import_mount_path=store.import_root)
    destination = tmp_path / 'transfer' / 'result.zarr'
    destination.parent.mkdir()
    real_write_labels_group = materialization.write_labels_group

    def write_an_empty_labels_group(node, names, version):
        real_write_labels_group(node, [], version)

    with patch('biomero_shallower.materialization.write_labels_group',
               side_effect=write_an_empty_labels_group):
        with pytest.raises(PixelIdentityError, match='label inventory differs'):
            materialize_shallow_zarr(registration.reference, destination, store.roots)
    assert not destination.exists()
    assert not list(destination.parent.glob('.*.biomero-materialize-*'))
