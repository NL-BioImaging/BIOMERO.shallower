"""Reject cross-format deduplication before opening fields or reading pixels."""
import json
from unittest.mock import patch

import pytest

from biomero_schema.zarr import TRANSFER_INPUT_MARKER
from biomero_shallower.operations import normalize
from biomero_shallower.result_zarr import evaluate_returned_zarr
from test_failures import snapshot
from test_ngff05 import make, manifest, provider
from test_result_zarr import _input, _make_image, _make_plate, _manifest, _plate_input


def make_store(root, version, plate):
    if version == 3:
        make(root, plate)
    elif plate:
        _make_plate(root, {"A/1/0": ("cells",), "B/1/0": ("cells",)})
    else:
        _make_image(root, labels=("cells",))


def input_for(version, plate):
    if version == 3:
        return manifest(plate).inputs[0]
    return _plate_input() if plate else _input(0)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("plate", [False, True])
@pytest.mark.parametrize("binding", ["marker", "artifact", "renamed"])
def test_cross_format_skips_discovery_and_identity(tmp_path, version, plate, binding):
    root = tmp_path / "result.zarr"
    make_store(root, version, plate)
    item = input_for(5 - version, plate).model_copy(update={
        "transfer_artifact": root.name if binding == "artifact" else "input.zarr",
    })
    if binding == "marker":
        (root / TRANSFER_INPUT_MARKER).write_text(json.dumps(item.to_dict()))
    before = snapshot(root)

    with patch("biomero_shallower.result_zarr.discover_ngff_nodes") as discover, \
            patch("biomero_shallower.result_zarr.IsccBioIdentityProvider") as identity:
        decision = evaluate_returned_zarr(root, _manifest(item))

    assert decision.outcome == "keep-full"
    assert decision.reason == "unsupported-canonical-profile"
    discover.assert_not_called()
    identity.assert_not_called()
    assert decision.image_identities == ()
    assert decision.label_identities == ()
    assert snapshot(root) == before


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("plate", [False, True])
def test_cross_format_normalize_retains_complete_store(tmp_path, version, plate):
    root = tmp_path / "result.zarr"
    make_store(root, version, plate)
    before = snapshot(root)
    identity = provider(plate)

    report = normalize(root, _manifest(input_for(5 - version, plate)),
                       identity_provider=identity)

    assert report.result == "kept-full"
    assert report.reason == "unsupported-canonical-profile"
    assert identity.calls == []
    after = snapshot(root)
    after.pop(".biomero-shallow-report.json")
    assert after == before


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("binding", ["marker", "artifact", "renamed"])
def test_mixed_inputs_preserve_binding_and_filter_renamed_candidates(tmp_path, version, binding):
    root = tmp_path / "result.zarr"
    make_store(root, version, False)
    incompatible = input_for(5 - version, False).model_copy(update={
        "transfer_artifact": root.name if binding == "artifact" else "input.zarr",
    })
    compatible = input_for(version, False).model_copy(update={
        "ordinal": 1, "selected_object_id": 2, "transfer_artifact": "other.zarr",
    })
    if binding == "marker":
        (root / TRANSFER_INPUT_MARKER).write_text(json.dumps(incompatible.to_dict()))
    identity = provider()

    decision = evaluate_returned_zarr(root, _manifest(incompatible, compatible),
                                      identity_provider=identity)

    if binding == "renamed":
        assert decision.eligible
        assert decision.matched_inputs == (compatible,)
        assert len(identity.calls) == 2  # image plus retained label
    else:
        assert decision.reason == "unsupported-canonical-profile"
        assert identity.calls == []


def test_ambiguous_artifact_does_not_become_unique_by_filtering_format(tmp_path):
    root = tmp_path / "result.zarr"
    make(root)
    items = [input_for(version, False).model_copy(update={
        "ordinal": version - 2, "transfer_artifact": root.name,
    }) for version in (2, 3)]
    identity = provider()

    decision = evaluate_returned_zarr(root, _manifest(*items), identity_provider=identity)

    assert decision.reason == "ambiguous-transfer-artifact"
    assert not decision.eligible
    assert identity.calls == []
