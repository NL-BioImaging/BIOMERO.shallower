# <img src="https://raw.githubusercontent.com/NL-BioImaging/OMERO.biomero/refs/tags/v1.2.1/webapp/src/img/biomero-logo.svg" alt="BIOMERO" height="28" style="height:28px; width:auto; vertical-align:middle;"> BIOMERO.shallower
[![Test and publish](https://github.com/NL-BioImaging/BIOMERO.shallower/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/NL-BioImaging/BIOMERO.shallower/actions/workflows/ci.yml)
> 🚀 **This package is part of <img src="https://raw.githubusercontent.com/NL-BioImaging/OMERO.biomero/refs/tags/v1.2.1/webapp/src/img/biomero-logo.svg" alt="BIOMERO" height="16" style="height:16px; width:auto; vertical-align:middle;"> BIOMERO 2.0** — For complete deployment and FAIR infrastructure setup, start with the [**NL-BIOMERO Documentation**](https://nl-bioimaging.github.io/NL-BIOMERO/) 📖

A filesystem-only library and CPU container for shallow-Zarr normalization.
It compares returned images and labels with the workflow's canonical inputs,
replaces verified duplicate arrays with references, and retains new or changed
data. No OMERO connection or credentials are required.

BIOMERO.importer uses the Python package locally. BIOMERO core runs the same
implementation on Slurm before archiving and transferring results.
Deduplication never suppresses result registration in OMERO.

Supports **contract 1, NGFF 0.4 / Zarr v2 Images and Plates**. Shallow storage
is optional; installing this package does not enable it. When shallow storage
is enabled, remote normalization is preferred unless explicitly disabled.

## Quick start

Python 3.11 or newer:

```sh
pip install 'biomero-shallower[identity]'
biomero-shallower --version
biomero-shallower inspect --returned-zarr /results/result.zarr
```

Container:

```sh
docker run --rm --network none cellularimagingcf/biomero-shallower:0.1.0 health
```

## Reconstruct a full Zarr

`materialize_shallow_zarr` can restore a standalone Image or Plate Zarr on disk,
without an OMERO script or connection. In an NL-BIOMERO deployment, open Python:

```sh
docker compose exec biomeroworker /opt/omero/server/venv3/bin/python
```

Replace the example paths below with paths **inside the container**:

```python
from pathlib import Path
from biomero_shallower.result_zarr import (
    load_managed_storage_roots,
    resolve_shallow_registration,
    materialize_shallow_zarr,
)

mount = Path("/data")
source = Path("/data/Project B/.analyzed/WORKFLOW/TIMESTAMP/result.ome.zarr")
destination = Path("/data/Project B/reconstructed-result.ome.zarr")
roots = load_managed_storage_roots(
    import_mount_path=mount,
    config_file="/opt/omero/server/biomero-config.json",
)
view = resolve_shallow_registration(
    source, storage_roots=roots, import_mount_path=mount,
)
if view is None:
    raise ValueError("No shallow collection found at the source path")
result = materialize_shallow_zarr(view.reference, destination, roots)
print(result.destination)
```

Use the whole result root for a Plate. The destination must not exist, its
parent must be writable, and there must be space for the full result. Original
stores remain unchanged. All referenced pixels and labels must be accessible.
The configuration path shown is the demo worker's group mapping configuration;
custom deployments must supply their own authoritative mappings, including
`group_mappings_file` if maintained separately. Outside the container, the same
Python API works with matching packages and storage mappings.

See the [reconstruction guide](https://nl-bioimaging.github.io/NL-BIOMERO/developer/biomero-shallow-zarr.html#reconstruct-shallow-zarr-on-disk)
for details. A shallow result itself is not a self-contained OME-Zarr for
generic readers; the reconstructed output is.

## Documentation

- [Usage and commands](https://nl-bioimaging.github.io/BIOMERO.shallower/)
- [Architecture, recovery and Python API](https://nl-bioimaging.github.io/BIOMERO.shallower/architecture/)
- [Development and testing](https://nl-bioimaging.github.io/BIOMERO.shallower/development/)
- [Release setup](https://nl-bioimaging.github.io/BIOMERO.shallower/releases/)

For local tests, install `.[test]` in a virtual environment and run
`python -m pytest tests -q`. GitHub Actions runs unit tests, container smoke
tests and a strict MkDocs build. GitHub releases publish the package and image.

## License

Apache-2.0. See [LICENSE](LICENSE). Dependencies retain their own licences.
