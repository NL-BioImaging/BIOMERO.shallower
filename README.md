# BIOMERO.shallower

> This package is part of **BIOMERO 2.0**. For complete deployment and
> infrastructure configuration, start with the
> [NL-BIOMERO documentation](https://nl-bioimaging.github.io/NL-BIOMERO/).

[Documentation](https://nl-bioimaging.github.io/BIOMERO.shallower/) ·
[Architecture and recovery](https://nl-bioimaging.github.io/BIOMERO.shallower/architecture/)

A filesystem-only, versioned result normalizer for BIOMERO. It compares returned
OME-Zarr pixels and labels with the workflow's canonical input identities, then
uses the shared shallow representation to omit duplicate arrays. It requires
no OMERO connection or credentials. The importer uses this same implementation
for its local path.

Supported adapter: contract **1**, **NGFF 0.4 / Zarr v2**, Image and Plate.
Unknown contracts are rejected. Existing inherited labels are referenced;
new and changed labels stay in the returned store. Label-free results can also
reference matching canonical pixels (including pixels matching another selected
input). Pixel deduplication removes duplicate
arrays on disk; it never suppresses registration of the result in OMERO.

## Integration with NL-BIOMERO

BIOMERO core runs this helper as a CPU-only Slurm job before archiving and
transferring workflow results. BIOMERO.scripts forwards its trusted receipts,
and BIOMERO.importer validates them before registering the shallow results.
The importer also uses this package for local normalization.

Shallow storage remains an optional feature. When shallow storage is enabled,
remote normalization is the preferred path; administrators can select local
normalization instead. Installing this package alone does not enable either
path. See the
[remote shallower administration guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html)
for feature flags, image acquisition, resources, and recovery.

## Installation and commands

Python 3.11 or newer is required. Install the `identity` extra when computing
real pixel identities:

```sh
pip install 'biomero-shallower[identity]'
```

```sh
biomero-shallower --version
biomero-shallower health
biomero-shallower inspect --returned-zarr /results/result.zarr
biomero-shallower normalize \
  --returned-zarr /results/result.zarr \
  --canonical-inputs /inputs/canonical.json \
  --report /results/report.json --contract-version 1 \
  --identity-workers 4 --failure-policy keep-full \
  --image cellularimagingcf/biomero-shallower:0.1.0
biomero-shallower verify \
  --returned-zarr /results/result.zarr \
  --canonical-inputs /inputs/canonical.json \
  --report /results/verification.json --contract-version 1 \
  --image cellularimagingcf/biomero-shallower:0.1.0
```

`normalize` generates complete image/label identities before committing.
`verify` validates a committed receipt and manifest without hashing pixels.
Exit **0** means a safe terminal result (normalized, kept full, or skipped);
exit **2** means invalid input, unsupported contract, or unresolved recovery.
JSON logs/results distinguish these outcomes. `health` imports the identity
provider without requiring image data.

The durable report is `.biomero-shallow-report.json` inside the store;
`--report` writes an additional machine-readable copy. It records tool/image,
contracts, input snapshot, eligibility, canonical and retained components,
timings, task/job provenance, and terminal result. No Slurm absolute paths occur
in the collection or report. `normalize-tree` is the internal batch interface;
it discovers outermost stores and emits `.biomero-shallow-batch.json` with
checksummed receipts. Other workflow outputs remain in place.

## Build and test

The container builds from this repository and installs the published schema
contracts. Its CPU runtime dependencies, including `biomero-schema==0.2.1b1`,
are pinned in `requirements.lock`.

From this repository:

```sh
docker build -t biomero-shallower:0.1.0 .
docker run --rm --network none biomero-shallower:0.1.0 health
mkdir -p /tmp/shallower-smoke
chmod 777 /tmp/shallower-smoke
docker run --rm --network none \
  -v /tmp/shallower-smoke:/fixture \
  -v "$PWD/tools:/tools:ro" \
  --entrypoint python biomero-shallower:0.1.0 /tools/mounted_smoke.py
```

The smoke fixture mount must be writable by UID 10001. Use an empty directory
for each run. The production image pins all transitive CPU dependencies in
`requirements.lock`; its build context allowlist excludes credentials and data.
The upstream BioIO dependency includes filesystem reader infrastructure, but
the image includes no OMERO, database client, Java, GPU runtime, or credentials.

For local unit tests, install `.[test]` in a repository-local Python 3.12
virtual environment, then run `python -m pytest tests -q`. Install the
`identity` extra for real hashing. The tests retain the original importer's
Image/Plate fixtures and decision/manifest assertions.

See [architecture and recovery](docs/architecture.md) for the transaction and
trust boundaries, and [benchmarks](docs/benchmarks.md) for measurement commands.
See [release setup](docs/releases.md) for Docker Hub credentials, package
publication, version checks, and the tested release workflow.

## License

Apache-2.0. See [LICENSE](LICENSE). Third-party dependencies retain their own
licences.

## Documentation development

In the repository-local virtual environment:

```sh
python -m pip install -e . -r docs/requirements.txt
python -m mkdocs serve
python -m mkdocs build --strict
```

Pull requests build the documentation; pushes to `main` publish GitHub Pages.
Select **Settings → Pages → Source → GitHub Actions** for this repository.
Generated `site/` output is not committed.
