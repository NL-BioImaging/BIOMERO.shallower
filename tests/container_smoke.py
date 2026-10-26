"""Small real-ISCC container acceptance test; writes only its fixture mount."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import UUID

import numpy as np
import zarr

from biomero_schema.zarr import CanonicalInputManifest, CanonicalInput, CanonicalZarrSource
from biomero_shallower import __version__
from biomero_shallower.pixel_identity import IsccBioIdentityProvider, read_zarr_semantic_guard
from biomero_shallower.result_zarr import resolve_shallow_registration, materialize_shallow_zarr

parser = argparse.ArgumentParser()
parser.add_argument("--zarr-format", type=int, choices=(2, 3), default=2)
version = parser.parse_args().zarr_format
ngff_version = "0.4" if version == 2 else "0.5"
profile = f"ngff-{ngff_version}-zarr-v{version}"
root = Path('/fixture') / f"v{version}"
canonical = root / 'canonical.zarr'
returned = root / 'out/result.zarr'
root.mkdir(exist_ok=True)
pixels = np.arange(64, dtype=np.uint16).reshape(1, 1, 1, 8, 8)


def create_image(path, data):
    group = zarr.open_group(str(path), mode='w', zarr_format=version)
    options = {"chunks": (1, 1, 1, 4, 4)}
    if version == 3:
        options["shards"] = (1, 1, 1, 8, 8)
    group.create_array('0', data=data, **options)
    multiscales = [{
        'axes': [{'name': name, 'type': kind} for name, kind in
                 [('t', 'time'), ('c', 'channel'), ('z', 'space'), ('y', 'space'), ('x', 'space')]],
        'datasets': [{'path': '0', 'coordinateTransformations': [
            {'type': 'scale', 'scale': [1, 1, 1, 1, 1]}]}],
    }]
    if version == 2:
        multiscales[0]["version"] = ngff_version
        group.attrs["multiscales"] = multiscales
    else:
        group.attrs["ome"] = {"version": ngff_version, "multiscales": multiscales}


create_image(canonical, pixels)
returned.parent.mkdir(exist_ok=True)
shutil.copytree(canonical, returned)
create_image(returned / 'labels/cells', (pixels > 20).astype(np.uint16))
labels = zarr.open_group(str(returned / 'labels'), mode='a', zarr_format=version)
if version == 2:
    labels.attrs['labels'] = ['cells']
else:
    labels.attrs['ome'] = {"version": ngff_version, "labels": ['cells']}
guard = read_zarr_semantic_guard(canonical, '.')
identity = IsccBioIdentityProvider().generate(
    canonical, node_path='.', role='image', shape=guard.shape, dtype=guard.dtype,
    axes=guard.axes, coordinate_transformations=guard.coordinate_transformations)
manifest = CanonicalInputManifest(
    workflowId=UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
    exportTaskId=UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'),
    inputs=(CanonicalInput(ordinal=0, selectedObjectType='Image', selectedObjectId=1,
                           transferArtifact='result.zarr', source=CanonicalZarrSource(
                               storageRoot='group-0-data', relativePath='canonical.zarr',
                               nodePath='.', sourceObjectType='Image', sourceObjectId=1,
                               sourceGeneration=1, interchangeProfile=profile,
                               pixelIdentity=identity, pixelIdentityOrigin='canonical-bootstrap',
                               canonicalPixelVerified=True)),))
(root / 'input.json').write_text(json.dumps(manifest.to_dict()))


def snapshot(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


before = snapshot(canonical)
os.environ['SLURM_JOB_ID'] = '123'
command = ['biomero-shallower', 'normalize-tree', '--returned-zarr', str(returned.parent),
           '--canonical-inputs', str(root / 'input.json'), '--contract-version', '1',
           '--report', str(root / 'batch.json'), '--image', f'biomero-shallower:{__version__}',
           '--task-id', 'cccccccc-cccc-cccc-cccc-cccccccccccc']
subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
assert not (returned / '0').exists()
assert (returned / 'labels/cells/0').exists()
report = (returned / '.biomero-shallow-report.json').read_bytes()
subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
assert (returned / '.biomero-shallow-report.json').read_bytes() == report
assert snapshot(canonical) == before
assert json.loads(report)["adapter"] == profile
roots = {"group-0-data": root, "import-mount-data": root}
view = resolve_shallow_registration(returned, storage_roots=roots, import_mount_path=root)
destination = root / "reconstructed.zarr"
materialize_shallow_zarr(view.reference, destination, roots)
np.testing.assert_array_equal(zarr.open_group(destination)["0"][:], pixels)
np.testing.assert_array_equal(
    zarr.open_group(destination)["labels/cells/0"][:], (pixels > 20).astype(np.uint16))
print(json.dumps({'result': 'passed', 'identity': 'real ISCC-BIO',
                  'profile': profile, 'reconstructedPixels': True,
                  'canonicalUnchanged': True, 'idempotent': True,
                  'normalization': json.loads(report)['timings']}))
