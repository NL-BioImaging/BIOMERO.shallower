# BIOMERO.shallower

BIOMERO.shallower is the shared filesystem-only implementation of shallow Zarr
in BIOMERO 2.0. It compares returned OME-Zarr images and labels with canonical
workflow inputs, references verified duplicate pixels, and retains new or
changed arrays. The importer runs it locally, or BIOMERO runs its CPU-only
container on Slurm before transferring results. It requires no OMERO connection
or credentials.

The current adapter writes the experimental BIOMERO shallow-manifest schema 2
for NGFF 0.4 / Zarr v2 Images and Plates. This private format is not RFC-8.
Deduplication never suppresses registration of the result in OMERO, and
installing this package does not enable the feature.

For feature flags, deployment, and image acquisition, see the
[NL-BIOMERO administration guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html).

## Installation

Python 3.11 or newer is required. Install the identity extra for real pixel
hashing:

```sh
pip install 'biomero-shallower[identity]'
biomero-shallower --version
biomero-shallower health
```

The package requires published receipt-capable schema contracts,
`biomero-schema>=0.3.0b1,<0.4`. The container pins the complete CPU identity
runtime in `requirements.lock`.

## Shallow one result

The canonical-input manifest is the trusted snapshot recorded for the workflow,
not a replacement generated from its returned metadata.

```sh
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

The `normalize` command omits only verified duplicate arrays, never the
canonical input.
New or changed labels remain in the store. Unknown contracts are rejected;
unsupported or ineligible full artifacts are retained. Four identity workers
are an explicit example, not the default: select parallelism for the allocated
CPU and filesystem resources.

`verify` validates the committed report and collection without rehashing
omitted pixels. It is not a checksum scan of retained pixel data and does not
make an untrusted sidecar authoritative. Remote imports also require the
orchestration receipt described in [Architecture and recovery](architecture.md).

Exit 0 means a safe terminal result: normalized, kept full, or skipped. Exit 2
means invalid input, an unsupported contract, or unresolved recovery. Inspect
the report's `result` and `reason` rather than interpreting exit 0 as proof that
all artifacts became shallow.

## Shallow a result directory

`normalize-tree` is the orchestration interface. It discovers outermost Zarr
stores and writes `.biomero-shallow-batch.json` with receipts for shallowed
artifacts. Ordinary non-Zarr outputs remain in place. Each artifact's durable
report is `.biomero-shallow-report.json`; `--report` writes an additional copy.
See the [report contracts](https://nl-bioimaging.github.io/biomero-schema/remote-shallower-contracts/).

## Container

```sh
docker run --rm --network none cellularimagingcf/biomero-shallower:0.1.0 health
```

The image runs as UID/GID `10001:10001`. Bind the returned result directory
writable and the canonical-input manifest read-only; the remote helper does not
need a mount of the canonical pixel stores themselves. Writable results require
POSIX locks, atomic rename, and fsync. Do not shallow output while a workflow
is still writing it.

For Slurm execution and SIF acquisition, follow the
[NL-BIOMERO deployment documentation](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html).
