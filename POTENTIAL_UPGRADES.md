# BIOMERO full-screen transfer and shallow-Zarr performance

## Scope

This report records the September 8, 2026 full-screen baseline and the
implementation options investigated before another long test. The baseline was
a detached `cisegmentation` workflow over plate 2005 with 846 image nodes. The
workflow returned one OME-Zarr with four labels per image.

The measurements below come from the worker, Slurm, importer, database, and
event-sourcing logs. They are wall-clock timings and include filesystem effects
from Docker Desktop's Windows bind mount where noted. They should not be treated
as general HPC benchmarks.

## Baseline identifiers

| Item | Identifier |
|---|---|
| Detached launcher workflow | `0f7e18da-7756-4cdd-8f4c-80cd26416f41` |
| Analysis workflow | `6350afa4-7312-4138-8beb-47a1d60c6fd4` |
| Import task | `9f710675-a898-440a-af78-05eea415e30d` |
| Import order | `28a7449e-5d66-4be6-9662-250a8255414e` |
| Returned result | `20220714_TKI_482__cisegmentation.ome.zarr` |

The retained Slurm result is at:

`/data/my-scratch/data/biomero_6350afa4-7312-4138-8beb-47a1d60c6fd4/data/out/20220714_TKI_482__cisegmentation.ome.zarr`

## Baseline timeline

| Stage | Start | End | Duration | Notes |
|---|---:|---:|---:|---|
| Detached launcher created | Sep 8 16:34:19 | | | |
| Cold canonical identity attempt | 18:12:47 | 18:21:49 | 9m 02s | 846 nodes, four workers; the later transfer used a cached identity snapshot. |
| Input ZIP creation | 21:56:44 | 22:36:31 | 39m 47.5s | Direct ZIP of the full input. |
| Input SCP | 22:36:31 | 22:38:23 | 1m 51.8s | |
| Remote unpack/conversion | 22:38:23 | 22:39:19 | about 55.8s | Conversion was effectively a no-op for this input. |
| Analysis | about 22:39 | Sep 9 about 03:27 | about 4h 48m | GPU workflow runtime. |
| Result ZIP creation | 03:27:18 | 03:29:27 | 2m 08.8s | 154,198 files and 115,168 directories. |
| Result SCP | 03:29:27 | 03:34:48 | 5m 21.1s | |
| Worker archive copy and extraction | 03:34:48 | 04:46:28 | 1h 11m 40.4s | Includes copying the received ZIP to permanent storage and extracting it on the Windows-backed `/data` mount. |
| First import attempt failed | 04:46:29 | | | The outer OMERO connection was stale after the long preparation phase. |
| Retry staged results | 05:15:43 | | | |
| Recursive result discovery | 05:15:43 | 05:46:56 | 31m 13s | Traversed 226,845 paths inside the outer Zarr unnecessarily. |
| Importer order detected | 05:46:59 | | | |
| Local identity evaluation | 05:47:00 | 06:29:55 | 42m 55s | Returned Zarr hashing for shallow storage. |
| Local normalization | 06:29:55 | 06:52:27 | 22m 32.6s | Removed/referenced duplicate pixel content. |
| OMERO registration | 06:52:27 | 06:54:54 | 2m 26s | |
| Importer order total | 05:46:59 | 06:54:54 | 1h 07m 55s | |

The old one-hour polling limit expired at 06:47:05, approximately 7m 49s
before the importer finished. The importer subsequently completed, but the
workflow task had already received a failure event.

## Data sizes and archive measurements

The returned result contained 8,968,870,301 bytes across 269,366 filesystem
entries. Existing shallow storage retained approximately 7-10% of the full
result.

| Format or method | Create time | Archive size | Observed result |
|---|---:|---:|---|
| ZIP, default Deflate | 132.306s | 7,696,668,188 bytes | Current result-transfer baseline. |
| ZIP, store only | 89.597s | 9,039,309,435 bytes | Faster packing, but the larger transfer outweighed the saving at the observed network rate. |
| ZIP, maximum Deflate | more than 453s | not retained | Compression alone exceeded the current ZIP-plus-SCP break-even point; stopped. |
| tar + Zstandard level 3 | 26.381s | 7,686,925,055 bytes | About 106s faster than Deflate ZIP, but only 9.7 MB (0.13%) smaller. |

The full tar.zst extracted to the Windows-backed `/data` mount in 39m 16.8s.
This is promising but is not a direct archive-format comparison: the current
1h 11m 40s block also copies the ZIP to permanent storage before extraction.

An 18-image result was used to compare local extractors on the same mount:

| Extractor/input | Duration |
|---|---:|
| Python `zipfile`, cold | 85.16s |
| Python `zipfile`, warm | 85.46s |
| Info-ZIP `unzip` | 201.94s |
| 7-Zip | 213.13s |
| Python `zipfile`, store-only ZIP | 72.75s |
| tar.zst extraction | 95.14s |

Replacing Python extraction with `unzip` or 7-Zip is therefore not beneficial
for this deployment. Increasing shallow identity workers is also not a local
quick win: measurements on the same development mount were 14.524s (one),
11.988s (two), 9.401s (four), 12.569s (eight), 13.857s (16), and 13.201s (32).
Four workers was the best observed setting.

## Improvements already implemented

The current feature branches include the following changes:

1. Result discovery stops descending after an outer `.zarr` is found. This
   targets the 31-minute recursive scan without changing label discovery inside
   the selected store.
2. Import status polling uses quick initial polls, backs off while unchanged,
   returns immediately on a terminal state, and permits imports lasting up to
   24 hours.
3. OMERO keepalive/reconnection covers both preprocessing and the expensive
   shallow-Zarr lifecycle preparation, preventing later metadata operations
   from reusing a stale outer connection.
4. Detached execution exits its polling loop immediately when all work is known
   to be complete rather than sleeping through another backoff interval.

These fixes address the failures observed in the baseline. They do not remove
the cost of local identity generation and normalization.

## Potential upgrades

### 1. Normalize on Slurm before archiving

Run the shallow-Zarr identity and normalization stage on the Slurm filesystem,
then archive and transfer only the retained 7-10%. This targets both the 65m 28s
local identity/normalization block and most result-transfer bytes. It also moves
the file-intensive operation away from the Windows Docker bind mount.

The `biomero-shallower` prototype implements this as an optional, versioned CPU
container with no OMERO or database access. It uses the canonical identity
snapshot already recorded by BIOMERO, emits validated receipts for the importer,
and keeps the existing local path when the feature is disabled. Before rollout,
it still needs a real Apptainer/Singularity Slurm smoke, a disposable full-screen
run, and end-to-end OMERO registration verification.

### 2. Remove the redundant permanent-storage archive copy

The current worker receives a temporary archive, copies it to permanent storage,
and then extracts it. Measure copy and extraction separately, then either receive
the archive directly into a staging location beside the final destination or
extract from the received archive into an atomic staging directory. This is a
simpler independent improvement even if ZIP remains the transfer format.

The final directory must only become visible after successful extraction and
validation. Interrupted staging data must be recognizable and safely removable
or resumable.

### 3. Stream an archive over SSH instead of staging a ZIP

A future transport can avoid both remote archive creation and local archive
storage by streaming a file-preserving archive:

```text
Slurm:  tar -C OUTPUT -cf - . | zstd -T0 -3
SSH:    binary stdout stream
Worker: zstd -dc | tar -xf - -C ATOMIC_STAGING_DIRECTORY
```

Compression, network transfer, and extraction can overlap. This can eliminate
the remote ZIP, the received temporary ZIP, and the permanent-storage copy.
Zstandard's principal benefit in the measurement was packing speed, not a
meaningfully smaller payload.

The implementation must stream with bounded memory and binary-safe backpressure;
it must not collect command output in RAM. A broken stream has no simple byte
resume, so extraction must target a workflow-specific staging directory and a
retry must restart or use a separately designed chunked protocol. Completion
needs an end-of-stream success code plus manifest/receipt validation before an
atomic publish. Direct recursive SCP/SFTP is unlikely to be competitive for
154,198 small files; the archive stream preserves batching without retaining the
archive.

As a lower-risk first step, transfer a tar.zst directly to permanent staging and
extract it there. This separates transport correctness from live streaming.

### 4. Make tar.zst an optional archive adapter

The full-result tar.zst was nearly the same size as Deflate ZIP but packed about
five times faster. An optional negotiated archive adapter could reduce packing
latency while retaining ZIP as the compatibility default. It requires matching
worker extraction support, checksum verification, cleanup/recovery semantics,
and measurements on a remotely normalized result. Once remote shallowing is in
use, archive creation may already be cheap enough that streaming and reduced
copying matter more than format selection.

### 5. Improve the cold canonical-identity path

The baseline transfer reused cached canonical identities; a genuinely cold run
adds at least the observed 9m 02s and may expose additional I/O. Future tests
must remove only the workflow's disposable canonical identity cache and measure:

- identity creation per image and per plate;
- cache serialization and lookup;
- transfer ZIP creation separately from identity generation;
- warm versus cold behavior;
- correctness when several workflows reuse the same canonical input.

Any optimization must retain exact pixel identity and the semantic guards used
to decide whether returned arrays are safe to reference.

## Recommended order

1. Complete the current control run with local shallowing and record the same
   stage boundaries.
2. Isolate permanent archive copy versus extraction time.
3. Run the standalone helper's health and small disposable fixture through the
   real Slurm/Apptainer path.
4. Benchmark remote shallowing on a disposable copy of the retained 846-image
   result, then verify receipts and retained labels.
5. Run the same workflow end to end with remote shallowing enabled and compare
   archive size, transfer, worker preparation, importer registration, event
   order, and final OMERO hierarchy.
6. Prototype direct-to-staging tar.zst, followed by bounded-memory SSH streaming
   only if the simpler path confirms a material end-to-end gain.

## Current control run

A second local-shallow control began on September 9, 2026 at 16:59 with workflow
`1d531791-ec97-4d9a-b5c1-2f53d325e275`. Its measured stages and outcome will be
added after completion.
