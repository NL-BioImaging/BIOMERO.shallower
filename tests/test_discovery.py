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
"""Finding returned stores."""

from biomero_shallower.discovery import find_returned_zarr_stores
from biomero_shallower.transaction import rollback_path


def test_stores_are_outermost_and_rollback_folders_are_not_searched(tmp_path):
    outer = tmp_path / 'results' / 'a.zarr'
    (outer / 'nested.zarr').mkdir(parents=True)
    rollback = rollback_path(tmp_path / 'results' / 'b.zarr', 'token')
    (rollback / 'moved' / 'inner.zarr').mkdir(parents=True)

    assert find_returned_zarr_stores(tmp_path) == (outer,)
