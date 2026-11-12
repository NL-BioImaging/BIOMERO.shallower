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
"""Real decoded pixels: no filesystem checksum, aliases or unrelated levels."""
import importlib
import numpy as np
import pytest
import zarr

from biomero_shallower.pixel_identity import (
    IsccBioIdentityProvider, read_zarr_semantic_guard,
    read_zarr_comparison_levels, pixel_identities_match,
)


def make_image(path, version=2, axes=('t', 'c', 'z', 'y', 'x'), dtype='uint16'):
    root = zarr.open_group(str(path), mode='w', zarr_format=version)
    shape = tuple({'t': 2, 'c': 2, 'z': 3, 'y': 8, 'x': 8}[axis] for axis in axes)
    data = np.arange(np.prod(shape), dtype=dtype).reshape(shape)
    datasets = []
    for name, stride in [('s0', 1), ('overview', 2)]:
        selection = tuple(slice(None, None, stride) if axis in ('x', 'y') else slice(None)
                          for axis in axes)
        pixels = data[selection]
        root.create_array(name, data=pixels, chunks=pixels.shape)
        datasets.append({'path': name, 'coordinateTransformations': [
            {'type': 'scale', 'scale': [stride if axis in ('x', 'y') else 1 for axis in axes]}]})
    ms = {'axes': [{'name': axis} for axis in axes], 'datasets': datasets}
    if version == 2:
        ms['version'] = '0.4'
        root.attrs.update({'multiscales': [ms]})
    else:
        root.attrs.update({'ome': {'version': '0.5', 'multiscales': [ms]}})
    return root, data


def generate(path, **kwargs):
    guard = read_zarr_semantic_guard(path, '.')
    return IsccBioIdentityProvider().generate(
        path, node_path='.', role='image', shape=guard.shape, dtype=guard.dtype,
        axes=guard.axes, coordinate_transformations=guard.coordinate_transformations, **kwargs)


@pytest.mark.parametrize('version', [2, 3])
def test_reads_only_selected_pixels_and_all_czt(tmp_path, monkeypatch, version):
    path = tmp_path / 'image.zarr'
    make_image(path, version)
    bio = importlib.import_module('iscc_bio.biocode')
    real_generate = bio.generate_biocode
    planes = []

    def collect(values):
        values = list(values)
        planes.extend(values)
        return real_generate(values)

    monkeypatch.setattr(bio, 'generate_biocode', collect)
    opened = []
    real_open = zarr.open_array

    def open_array(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(zarr, 'open_array', open_array)
    value = generate(path)
    assert value.is_current_profile
    assert value.dataset_path == 'overview'
    assert value.comparison_shape == (2, 2, 3, 4, 4)
    assert len(planes) == 12
    assert [(p.z_depth, p.c_channel, p.t_time) for p in planes] == [
        (z, c, t) for z in range(3) for c in range(2) for t in range(2)]
    assert len(opened) == 1 and opened[0].endswith('overview')


@pytest.mark.parametrize('version', [2, 3])
def test_full_resolution_codes_preserve_upstream_pixel_encoding(tmp_path, version):
    path = tmp_path / 'image.zarr'
    _, data = make_image(path, version)
    from iscc_bio.biocode import generate_biocode
    from iscc_bio.imagewalk.common import Plane
    expected = generate_biocode(Plane(data[t, c, z], 0, z, c, t)
                               for z in range(3) for c in range(2) for t in range(2))
    actual = generate(path, full_resolution=True)
    assert actual.instance_code == expected[0]['units'][1]
    assert actual.dataset_path == 's0'
    assert actual.comparison_shape == actual.shape


def test_equal_selected_pixels_do_not_claim_full_resolution_proof(tmp_path):
    path = tmp_path / 'image.zarr'
    root, _ = make_image(path)
    before = generate(path)
    full_before = generate(path, full_resolution=True)
    root['s0'][0, 0, 0, 1, 1] = 42
    assert pixel_identities_match(before, generate(path))
    assert not pixel_identities_match(full_before, generate(path, full_resolution=True))
    assert not pixel_identities_match(before, full_before)
    assert not pixel_identities_match(before, before.model_copy(update={'comparison_profile': None}))


def test_level_with_reduced_z_is_ineligible(tmp_path):
    path = tmp_path / 'image.zarr'
    root, _ = make_image(path)
    root.create_array('wrong', shape=(2, 2, 1, 1, 1), dtype='uint16')
    attrs = dict(root.attrs)
    attrs['multiscales'][0]['datasets'].append({'path': 'wrong'})
    root.attrs.update(attrs)
    assert [level.dataset_path for level in read_zarr_comparison_levels(path, '.')] == ['overview', 's0']


@pytest.mark.parametrize('version', [2, 3])
def test_axis_order_and_big_endian_decode_keep_canonical_plane_bytes(tmp_path, version):
    path = tmp_path / 'image.zarr'
    _, data = make_image(path, version, axes=('x', 'y', 'z', 'c', 't'), dtype='>u2')
    from iscc_bio.biocode import generate_biocode
    from iscc_bio.imagewalk.common import Plane
    expected = generate_biocode(Plane(data[:, :, z, c, t].T, 0, z, c, t)
                               for z in range(3) for c in range(2) for t in range(2))
    assert generate(path, full_resolution=True).instance_code == expected[0]['units'][1]
