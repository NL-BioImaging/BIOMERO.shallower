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
"""
Public import path for returned-store discovery, evaluation, pruning and materialization.

The implementation lives in cohesive modules; this module re-exports the API
that BIOMERO.importer and biomero-scripts import, so their paths keep working:

- `discovery`: find returned stores and their NGFF nodes.
- `evaluation`: compare a store with the canonical inputs and decide.
- `normalization`: prune the duplicate arrays transactionally.
- `registration`: resolve managed sources and shallow registrations.
- `materialization`: rebuild a conventional Zarr from a shallow reference.
"""

from .discovery import NgffNode, discover_ngff_nodes, find_returned_zarr_stores
from .evaluation import (
    ReturnedZarrDecision,
    evaluate_returned_zarr,
    is_failure_reason,
    remove_transfer_input_marker,
)
from .materialization import MaterializedShallowResult, materialize_shallow_zarr
from .normalization import NormalizedShallowResult, normalize_returned_zarr
from .registration import (
    ShallowRegistration,
    load_managed_storage_roots,
    resolve_managed_source_path,
    resolve_shallow_registration,
)

__all__ = [
    "NgffNode",
    "MaterializedShallowResult",
    "NormalizedShallowResult",
    "ReturnedZarrDecision",
    "ShallowRegistration",
    "discover_ngff_nodes",
    "evaluate_returned_zarr",
    "find_returned_zarr_stores",
    "is_failure_reason",
    "load_managed_storage_roots",
    "materialize_shallow_zarr",
    "normalize_returned_zarr",
    "remove_transfer_input_marker",
    "resolve_managed_source_path",
    "resolve_shallow_registration",
]
