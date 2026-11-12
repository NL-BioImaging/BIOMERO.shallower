"""Operational contract adapters; schemas remain in biomero-schema."""


class Ngff04ZarrV2:
    """Version 1 preserves the original importer algorithms and semantics."""

    profile = "ngff-0.4-zarr-v2"

    # The evaluation and normalization modules are imported on use so that
    # importing this module (and so `capabilities`) stays light.
    def evaluate(self, root, manifest, **kwargs):
        from .evaluation import evaluate_returned_zarr
        return evaluate_returned_zarr(root, manifest, **kwargs)

    def normalize(self, decision, workflow_id, **kwargs):
        from .normalization import normalize_returned_zarr
        return normalize_returned_zarr(decision, workflow_id, **kwargs)


ADAPTERS = {1: Ngff04ZarrV2()}


class Ngff05ZarrV3(Ngff04ZarrV2):
    """Same workflow contracts, using NGFF 0.5 metadata and v3 arrays."""

    profile = "ngff-0.5-zarr-v3"


FORMAT_ADAPTERS = {2: ADAPTERS[1], 3: Ngff05ZarrV3()}
