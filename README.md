# BIOMERO.shallower

> Part of **BIOMERO 2.0**. For deployment and infrastructure configuration,
> see the [NL-BIOMERO documentation](https://nl-bioimaging.github.io/NL-BIOMERO/).

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
