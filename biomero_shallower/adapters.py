"""Operational contract adapters; schemas remain in biomero-schema."""


class Ngff04ZarrV2:
    """Version 1 preserves the original importer algorithms and semantics."""

    profile = "ngff-0.4-zarr-v2"

    def evaluate(self, root, manifest, **kwargs):
        from .result_zarr import evaluate_returned_zarr
        return evaluate_returned_zarr(root, manifest, **kwargs)

    def normalize(self, decision, workflow_id, **kwargs):
        from .result_zarr import normalize_returned_zarr
        return normalize_returned_zarr(decision, workflow_id, **kwargs)


ADAPTERS = {1: Ngff04ZarrV2()}
