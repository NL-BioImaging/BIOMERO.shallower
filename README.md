# BIOMERO.shallower

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

```sh
biomero-shallower --version
biomero-shallower health
biomero-shallower inspect --returned-zarr /results/result.zarr
biomero-shallower normalize \
  --returned-zarr /results/result.zarr \
  --canonical-inputs /inputs/canonical.json \
  --report /results/report.json --contract-version 1 \
  --identity-workers 4 --failure-policy keep-full \
  --image registry.example/biomero-shallower:0.1.0
biomero-shallower verify \
  --returned-zarr /results/result.zarr \
  --canonical-inputs /inputs/canonical.json \
  --report /results/verification.json --contract-version 1 \
  --image registry.example/biomero-shallower:0.1.0
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

Use sibling `biomero-schema` source containing the remote receipt contracts.
The schema wheel is built as `0.2.1.dev1`; publish a matching release before
deploying package requirements through a public index. The schema version is
set by a build environment variable without modifying its project metadata.

From the common workspace parent:

```sh
docker build -f biomero-shallower/Dockerfile -t biomero-shallower:0.1.0 .
docker run --rm --network none biomero-shallower:0.1.0 health
mkdir -p /tmp/shallower-smoke
docker run --rm --network none \
  -v /tmp/shallower-smoke:/fixture \
  -v "$PWD/biomero-shallower/tools:/tools:ro" \
  --entrypoint python biomero-shallower:0.1.0 /tools/mounted_smoke.py
```

The smoke fixture mount must be writable by UID 10001. Use an empty directory
for each run. The production image pins all transitive CPU dependencies in
`requirements.lock`; its build context allowlist excludes credentials and data.
The upstream BioIO dependency includes filesystem reader infrastructure, but
the image includes no OMERO, database client, Java, GPU runtime, or credentials.

For local unit tests, install the sibling schema and this package in a Python
3.12 virtual environment, then run `python -m pytest tests -q`. Install the
`identity` extra for real hashing. The tests retain the original importer's
Image/Plate fixtures and decision/manifest assertions.

See [architecture and recovery](docs/architecture.md) for the transaction and
trust boundaries, and [benchmarks](docs/benchmarks.md) for measurement commands.
