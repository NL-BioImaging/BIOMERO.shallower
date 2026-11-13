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
"""The ``biomero`` pointer on shallow image nodes: wire format and validation."""
import json

import pytest
from biomero_schema.zarr import ShallowNodePointer

from test_ngff05 import make, manifest as v3_manifest, provider as v3_provider
from test_result_zarr import IdentityProvider, _identity, _input, _make_image, _manifest
from biomero_shallower.ngff import read_attrs
from biomero_shallower.operations import normalize, validate_report

# The pointer as written before ShallowNodePointer was used. Files are written
# with sorted keys, so spelling and values are what the format pins.
EXPECTED_POINTER_JSON = (
    '{"format": "biomero-shallow-zarr", "manifest": ".biomero-shallow.json", '
    '"workflowId": "%s"}'
)
INVALID_POINTERS = {
    "missing": None,
    "not an object": "biomero-shallow-zarr",
    "wrong manifest": {
        "format": "biomero-shallow-zarr", "manifest": "other.json",
        "workflowId": "WORKFLOW",
    },
    "schema-1 model": {
        "model": "rfc8-shallow-copy", "manifest": ".biomero-shallow.json",
        "workflowId": "WORKFLOW",
    },
    "bad workflow id": {
        "format": "biomero-shallow-zarr", "manifest": ".biomero-shallow.json",
        "workflowId": "not-a-uuid",
    },
}


def _normalized_store(tmp_path, zarr_format):
    """Normalize a one-image store; return its root and canonical inputs."""
    if zarr_format == 3:
        inputs = v3_manifest()
        source = tmp_path / inputs.inputs[0].source.relative_path
        make(source)
        root = tmp_path / "result.zarr"
        make(root)
        normalize(root, inputs, identity_provider=v3_provider())
        return root, inputs
    inputs = _manifest(_input(0))
    _make_image(tmp_path / inputs.inputs[0].source.relative_path, labels=("cells",))
    root = tmp_path / "result.zarr"
    _make_image(root, labels=("cells",))
    normalize(root, inputs, identity_provider=IdentityProvider(_identity()))
    return root, inputs


def _raw_pointer(root, zarr_format):
    """Return the persisted ``biomero`` object from ``.zattrs`` or ``zarr.json``."""
    if zarr_format == 3:
        doc = json.loads((root / "zarr.json").read_text(encoding="utf-8"))
        return doc["attributes"]["ome"]["biomero"]
    return json.loads((root / ".zattrs").read_text(encoding="utf-8"))["biomero"]


def _write_pointer(root, zarr_format, pointer):
    """Replace (or with None, remove) the persisted ``biomero`` object."""
    path = root / ("zarr.json" if zarr_format == 3 else ".zattrs")
    doc = json.loads(path.read_text(encoding="utf-8"))
    attrs = doc["attributes"]["ome"] if zarr_format == 3 else doc
    if pointer is None:
        attrs.pop("biomero")
    else:
        attrs["biomero"] = pointer
    path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.mark.parametrize("zarr_format", [2, 3])
def test_written_pointer_is_byte_identical_and_valid(tmp_path, zarr_format):
    root, inputs = _normalized_store(tmp_path, zarr_format)
    pointer = _raw_pointer(root, zarr_format)
    workflow_id = str(validate_report(root, inputs).manifest.workflow_id)
    assert json.dumps(pointer) == EXPECTED_POINTER_JSON % workflow_id
    assert ShallowNodePointer.from_dict(pointer).to_dict() == pointer
    assert read_attrs(root)["biomero"] == pointer


@pytest.mark.parametrize("zarr_format", [2, 3])
@pytest.mark.parametrize("case", sorted(INVALID_POINTERS))
def test_validation_rejects_missing_or_invalid_pointer(tmp_path, zarr_format, case):
    root, inputs = _normalized_store(tmp_path, zarr_format)
    workflow_id = _raw_pointer(root, zarr_format)["workflowId"]
    pointer = INVALID_POINTERS[case]
    if isinstance(pointer, dict):
        pointer = {k: workflow_id if v == "WORKFLOW" else v for k, v in pointer.items()}
    _write_pointer(root, zarr_format, pointer)
    with pytest.raises(ValueError, match="Invalid shallow image structure"):
        validate_report(root, inputs)
