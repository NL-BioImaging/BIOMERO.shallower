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
Operational contract adapters; schemas remain in biomero-schema.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from biomero_schema.zarr import CanonicalInputManifest

    from .evaluation import ReturnedZarrDecision
    from .normalization import NormalizedShallowResult


class Ngff04ZarrV2:
    """
    Version 1 preserves the original importer algorithms and semantics.

    Attributes:
        profile (str): The interchange profile this adapter handles.
    """

    profile = "ngff-0.4-zarr-v2"

    # The evaluation and normalization modules are imported on use so that
    # importing this module (and so `capabilities`) stays light.
    def evaluate(
        self,
        root: Path,
        manifest: "CanonicalInputManifest | None",
        **kwargs: Any,
    ) -> "ReturnedZarrDecision":
        """
        Decide whether the returned store at ``root`` may be shallowed.

        Args:
            root (Path): The returned Zarr store.
            manifest (CanonicalInputManifest | None): The workflow's canonical
                input snapshot.
            **kwargs: Passed on to `evaluate_returned_zarr`.

        Returns:
            ReturnedZarrDecision:
                The keep-full, eligible or passthrough decision.

        Raises:
            ValueError: If the identity workers setting is not a positive
                integer.
        """
        from .evaluation import evaluate_returned_zarr
        return evaluate_returned_zarr(root, manifest, **kwargs)

    def normalize(
        self,
        decision: "ReturnedZarrDecision",
        workflow_id: "UUID | str",
        **kwargs: Any,
    ) -> "NormalizedShallowResult":
        """
        Prune the store an eligible decision describes.

        Args:
            decision (ReturnedZarrDecision): An eligible decision.
            workflow_id (UUID | str): The workflow the store belongs to.
            **kwargs: Passed on to `normalize_returned_zarr`.

        Returns:
            NormalizedShallowResult:
                The outcome of the transactional prune.
        """
        from .normalization import normalize_returned_zarr
        return normalize_returned_zarr(decision, workflow_id, **kwargs)


ADAPTERS = {1: Ngff04ZarrV2()}


class Ngff05ZarrV3(Ngff04ZarrV2):
    """
    Same workflow contracts, using NGFF 0.5 metadata and v3 arrays.

    Attributes:
        profile (str): The interchange profile this adapter handles.
    """

    profile = "ngff-0.5-zarr-v3"


FORMAT_ADAPTERS = {2: ADAPTERS[1], 3: Ngff05ZarrV3()}
