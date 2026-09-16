# BIOMERO shallow-Zarr benchmark history

Last updated: 2026-09-16

## Purpose

This is the consolidated ledger for the shallow-Zarr measurements collected
during local development and ACC prerelease testing. It keeps correctness runs,
microbenchmarks, failed attempts, and complete workflows separate so that a
fast isolated stage is not mistaken for a faster end-to-end workflow.

The detailed September 2026 worker and importer timeline remains in
[`POTENTIAL_UPGRADES.md`](POTENTIAL_UPGRADES.md). Product behavior and the
storage contract are documented in
[NL-BIOMERO's shallow-Zarr developer documentation](https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/docs/developer/biomero-shallow-zarr.md).

## Interpretation rules

- The large Plate has **846 images across 94 wells**. It is referred to below
  as the 846-image Plate, not an 846-well Plate.
- Source export/canonical indexing and returned-result identity evaluation are
  different stages. Their durations must not be compared as though they were
  the same operation.
- A cached canonical lookup is not a cold identity benchmark.
- `Identity + normalization` is the shallow-specific local processing cost.
  Archive copy/extraction and OMERO registration are part of the surrounding
  return path and would not necessarily disappear merely by disabling shallow
  storage.
- Failed runs are useful for throughput up to the failure point, but they do
  not establish a complete-workflow duration or successful storage reduction.
- Times are single observations unless explicitly described as means.

## Software provenance and release mapping

The exact development commit is the primary provenance for the local runs.
Package versions containing `.devN+g<sha>` identify an unreleased branch build:
for example, `2.9.0b5.dev16+g14ded96b4` is a development checkout based on the
beta.5 line, not the beta.5 release itself.

Several feature branches were squash-merged. Their development SHAs are
therefore not literal ancestors of the eventual release tag. In the tables
below, **first release carrying the changes** means the first public prerelease
whose squash commit contains the corresponding implementation. That release
may also contain later commits and is not claimed to be byte-identical to the
image used for the benchmark.

### Run-to-build ledger

| Run or Slurm jobs | Component | Exact deployed version or commit | Build classification | First release carrying the changes | Confidence/source |
|---|---|---|---|---|---|
| Small Plate, jobs 570-571 | BIOMERO | `2.9.0b4.dev11+g9dbd081a8`; `9dbd081a817305560d7927fb0239b13a7aaa363c` | Local `session-poller` development image | `v2.9.0-beta.6`, functionally via squash merge of PR 31 | High; retained worker images built before these jobs |
| Small Plate, jobs 570-571 | biomero-scripts | `cc240fb9f6b8f64d7e03ee27b7b0b95ad1afdbcf` | Local `session-poller` development checkout | `v2.9.0-beta.6`, functionally via squash merge of PR 12 | High; retained worker/server Git checkout |
| Small Plate, job 572 | BIOMERO | `2.9.0b4.dev13+g1fedb5fe2`; `1fedb5fe2322f66729df4a17923e690662f27d32` | Local `session-poller` development image | `v2.9.0-beta.6`, functionally via squash merge of PR 31 | High; retained worker image |
| Small Plate, job 572 | biomero-scripts | `cc240fb9f6b8f64d7e03ee27b7b0b95ad1afdbcf` | Local `session-poller` development checkout | `v2.9.0-beta.6`, functionally via squash merge of PR 12 | High; retained worker Git checkout |
| Small Plate, jobs 570-572 | BIOMERO.importer | `v1.5.0-beta.2`; `74b6649acc122a1afdc37d04cf80792e2fc1e0c2` | Tagged release | `v1.5.0-beta.2`, exact | High; retained importer source fingerprint and release files |
| September 8 large Plate, job 574 | BIOMERO | `2.9.0b5.dev16+g14ded96b4`; `14ded96b49dc9d7b68dada642f6c6468b0b3dd52` | Local `session-poller` development image | `v2.9.0-beta.6`, functionally via squash merge of PR 31 | Exact; installed package metadata in retained worker image |
| September 8 large Plate, job 574 | biomero-scripts | `990c6bee01fb90ab5429502f7a03b87ee9520a7e` | Local `session-poller` development checkout | `v2.9.0-beta.6`, functionally via squash merge of PR 12 | Exact; retained worker and server Git checkouts |
| September 8 large Plate, job 574 | BIOMERO.importer | `v1.5.0-beta.2`; `74b6649acc122a1afdc37d04cf80792e2fc1e0c2` | Tagged release | `v1.5.0-beta.2`, exact | High; retained importer source fingerprint and release files |
| September 9 control, job 575 | BIOMERO | `2.9.0b5.dev16+g14ded96b4`; `14ded96b49dc9d7b68dada642f6c6468b0b3dd52` | Local `session-poller` development image | `v2.9.0-beta.6`, functionally via squash merge of PR 31 | Exact; installed package metadata in retained worker image |
| September 9 control, job 575 | biomero-scripts | `d04b8ed8e0e347338ca6165b56e972e96f3595e8` | Local `session-poller` development checkout | `v2.9.0-beta.6`, functionally via squash merge of PR 12 | Exact; retained worker and server Git checkouts |
| September 9 control, first import attempt | BIOMERO.importer | `61d6f40afc40e177824f97060aa3dd7f0ed1436a` | Local build, beta.2 plus one commit | `v1.5.0-beta.4`, functionally via squash merge of PR 20 | Exact source fingerprint; image package metadata itself says only `0.0.0` |
| September 9 control, successful recovery import | BIOMERO.importer | `a8a2cd2758d2e7264db3d5c4a30c53792de277e6` | Local build, beta.2 plus two commits | `v1.5.0-beta.4`, functionally via squash merge of PR 20 | Exact source fingerprint; image package metadata itself says only `0.0.0` |

All retained local images above used `biomero-schema 0.2` and
`iscc-bio 0.2.0`. The simple Plate workflow reported
`simple-zarr-plate-processor v0.3.0`; the BIOMERO script metadata itself
reported the API/script version as `2.9.0`, which is not precise enough to
replace the commit-level evidence.

The top-level local deployment was an untagged NL-BIOMERO `session-poller`
checkout. Its exact repository SHA was not embedded in the Docker images and
cannot be reconstructed reliably from the runtime logs. The feature bundle was
first published as NL-BIOMERO `v1.8.0-beta.2` at `78650afdd27ca98239674b0adda1e3a4516a4757`;
that is the first matching public release, not an assertion that the local
checkout was identical. Current `.env` values must not be used as historical
evidence because they have changed since these runs.

### ACC prerelease ledger

| Attempt | NL-BIOMERO release | BIOMERO | biomero-scripts | BIOMERO.importer | OMERO.biomero | Evidence |
|---|---|---|---|---|---|---|
| Small 18-image Plate relative-alias failure | Not preserved | Not preserved | Not preserved | `v1.5.0-beta.4` | Not preserved | Importer version from the captured failure diagnosis; do not infer the other versions |
| Plate 252 input-preparation failure | `v1.8.0-beta.4`; `f050d2cf42143d03cf62371528ee721e1789dc94` | `2.9.0b7` | `v2.9.0-beta.7` | `1.5.0b5` | `1.7.0b3` | Reported deployed NL-BIOMERO release plus that tag's `.env` build/deployment manifest |
| `A-FULL-HCS` successful full integration | `1.8.0-beta.5`; `aad7fffea6d803276a8cfe57e7e233991a861ec8` | `2.9.0b6` | `v2.9.0-beta.6` | `1.5.0-beta.5` | `1.7.0b3` | Runtime versions recorded by the ACC integration test |
| Three-model integration, workflow `3999cf8b…` | Not supplied | Not supplied | Not supplied | Not supplied | Not supplied | New ACC run; do not inherit versions from the preceding one-model run without confirmation |

The Plate 252 component versions are the versions declared by the released
NL-BIOMERO manifest and are consistent with the reported deployment. They were
not independently extracted from ACC container package metadata. In
particular, the `v1.8.0-beta.4` Git tag is the release identity; the
`NL_BIOMERO_VERSION=1.8.0b3` value still present inside that tag's `.env` was
the Docker image reference used by Compose and must not be mistaken for the Git
release tag.

For the successful `A-FULL-HCS` run, the runtime-recorded BIOMERO and scripts
versions match `v1.8.0-beta.5`'s `.env.shared`, not its demo `.env`. The
runtime importer was beta.5 rather than `.env.shared`'s beta.4. Treat this as
an NL-BIOMERO beta.5 deployment with explicitly recorded component versions,
not as proof that every container came unchanged from the demo release's
default version set.

## Executive summary

| Observation | Dataset | Storage result | Shallow-specific processing | End-to-end status |
|---|---|---:|---:|---|
| Local production-path benchmark | 18 images, one new label each | 92.6% saved | about 25.4s | Isolated importer benchmark |
| Local five-Image live batch | Five Images with new and inherited labels | 91.9% saved | about 10s | Successful |
| Local multi-generation Image | One Image with five inherited and four new labels | 88.9% saved | 19.7s | Successful |
| Windows/Docker large-Plate control | 846 images, four labels each | Approximately 90-93% inferred saved | 1h03m26.9s | Successful after recoverable connection failure |
| ACC Plate 252 attempt | 846 source images | Not reached | Return-side shallowing not reached | Failed after 1h03m35s during input preparation |
| ACC small Plate prerelease | 18 source images | Not established | Not reached successfully | Failed during cold canonical identity aliasing |
| ACC small Plate validation | 18 source images | 1,803,585 B retained; full size not recorded | 33.42s | Successful in 4m03.21s |
| ACC `A-FULL-HCS` | 846 images, one cell label each | 540,582,567 B retained; full size not recorded | 45m08.3s | Successful in 2h04m27.7s |
| ACC three-model integration | 846 images, four labels each | 6,630,940,825 B avoided (83.91%) | 1h31m17s | Successful in 3h39m36s |

The often-used shorthand that the large Plate saved roughly 90% of disk space
"at a cost of about two hours" needs qualification:

| Large-Plate return-path accounting | Duration | Attribution |
|---|---:|---|
| Permanent archive copy and extraction | 1h11m34.5s | Existing worker/storage path, not identity work |
| Returned-result identity evaluation | 41m59.5s | Shallow-specific |
| Transactional normalization | 21m27.4s | Shallow-specific |
| OMERO registration | 2m23.7s | Import path |
| Discovery, terminal detection, and finalization | about 33s | Import/finalization path after discovery fix |
| **Identity plus normalization** | **1h03m26.9s** | **Direct local shallow cost** |
| **Clean post-result-transfer path** | **about 2h18m** | **Copy/extract plus shallow/import/finalization** |

Remote normalization is intended to reduce more than the direct 1h03m local
shallow block: it should also prevent duplicate source pixels from entering the
result archive, crossing the network, and being extracted onto the slow
Windows-backed `/data` mount. That gain has not yet been measured end to end.

## Dataset inventory

### Small Plate

- 18 image fields.
- One new label per field in the primary benchmark.
- 1,722 files in the returned result.
- Used for storage, identity-worker scaling, and archive extraction tests.

A detached `simple-zarr-plate-processor` observation with workflow
`3a300a43-8bcb-4912-8a52-e2711d0f25d3` was reported as approximately 33
minutes end to end. Its exact result size, terminal timestamps, cache state,
and stage breakdown were not preserved in the benchmark notes, so it is useful
only as historical context and not as a comparison baseline.

### Large full-screen Plate

- OMERO Plate 2005 in the local development deployment.
- 94 wells and 846 image nodes.
- `cisegmentation` returned four label sets per image: 3,384 label components.
- Successful control produced 1,029,249 objects, 2,058,498 intensity rows, and
  1,420,786 relationships.
- The retained full result used for archive tests contained 154,198 files and
  115,168 directories, or 269,366 filesystem entries.

### ACC Plate 252

- 846 source images.
- The first recorded attempt failed during input preparation and did not reach
  segmentation or return-side normalization.
- The later `A-FULL-HCS` validation reused the generation-1 canonical Plate,
  ran Cellpose `cyto3` only, and returned one `labels_cells` component for each
  of the 846 Images.

## Local compute environment and Slurm allocations

The local Slurm accounting database is persistent and still contains the jobs
used for these measurements. Accounting records the generic GRES allocation;
the GPU model comes from `nvidia-smi` inside the same `c1` worker node.

### GPU node

| Property | Recorded or currently observed value |
|---|---|
| Slurm node | `c1` |
| Slurm partitions | `normal` and `gpu` |
| Slurm node configuration | 8 CPUs, 5,120 MiB real memory, `gpu:1` |
| GPU | NVIDIA GeForce RTX 3060 |
| GPU memory | 12,288 MiB |
| Compute capability | 8.6 |
| Driver observed on 2026-09-15 | 572.16 |
| CPU visible inside `c1` | Intel Xeon w3-2423, 6 cores/12 threads |
| Docker hard CPU/memory limit | None configured |

The historical accounting row proves that each cisegmentation job below ran on
`c1` with `gres/gpu=1`. It does not store the GPU product name. The RTX 3060
attribution is therefore confirmed by the current one-GPU `c1` mapping and is
valid provided that the host GPU or node mapping was not replaced after the
September runs.

| Slurm job | Workload | State | Runtime | Partition/node | Allocation |
|---:|---|---|---:|---|---|
| 568 | Five-Image cisegmentation batch | Completed | 2m35s | `gpu` / `c1` | 4 CPUs, 5 GiB, 1 GPU |
| 569 | Five-Image cisegmentation batch | Completed | 3m38s | `gpu` / `c1` | 4 CPUs, 5 GiB, 1 GPU |
| 570 | Small Plate simple Zarr processor | Completed | 31s | `normal` / `c1` | 4 CPUs, 5 GiB, no GPU requested |
| 571 | Small Plate simple Zarr processor | Completed | 34s | `normal` / `c1` | 4 CPUs, 5 GiB, no GPU requested |
| 572 | Small Plate simple Zarr processor | Completed | 36s | `normal` / `c1` | 4 CPUs, 5 GiB, no GPU requested |
| 573 | Large-Plate cisegmentation attempt | Timed out | 45m10s | `gpu` / `c1` | 4 CPUs, 5 GiB, 1 GPU |
| 574 | September 8 large-Plate analysis | Completed | 4h47m19s | `gpu` / `c1` | 4 CPUs, 5 GiB, 1 GPU |
| 575 | September 9 control analysis | Completed | 4h38m10s | `gpu` / `c1` | 4 CPUs, 5 GiB, 1 GPU |

Job 573 was an earlier large-Plate attempt with an insufficient 45-minute time
limit. Jobs 574 and 575 are the two complete large-Plate GPU measurements.

The approximately 33-minute end-to-end small Plate observation should not be
described as 33 minutes of computation: its matching simple-processing jobs
took only 31-36 seconds on Slurm. Most elapsed time was outside the scientific
task, in export, transfer, import, shallow processing, and finalization.

Likewise, the 41m59.5s identity and 21m27.4s normalization measurements for the
846-image control did **not** run on the RTX 3060. Those were CPU/filesystem
operations in the NL-BIOMERO worker/importer containers on Docker Desktop's
Windows-backed storage. The GPU attribution applies to the cisegmentation
analysis stage. The optional standalone remote shallower is also designed as a
CPU Slurm job and has not yet produced a full-screen cluster timing.

## Small-result storage and processing measurements

| Scenario | Full or estimated full | Stored shallow | Avoided | Processing or reconstruction |
|---|---:|---:|---:|---:|
| 18-image Plate | 146,143,912 B | 10,775,929 B | 135,367,983 B (92.6%) | 12.653s identity mean + 12.729s normalization mean = about 25.4s |
| ACC 18-image validation | Not retained | 1,803,585 B | Cannot calculate without the pre-shallow size | 25.68s identity + 7.74s normalization = 33.42s |
| Five-Image live batch | 28.463 MiB estimated | 2.319 MiB | 26.144 MiB (91.9%) | About 10s identity and normalization total |
| Multi-generation Image | 8,956,291 B estimated | 990,300 B | 7,965,991 B (88.9%) | 18.3s identity + 1.4s normalization; 7.6s outbound reconstruction |
| Earlier individual Image | 6,848,883 B | 185,072 B | 6,663,811 B (97.3%) | Not measured |

The first diagnostic implementation took approximately 196 seconds to
normalize the 18-image Plate. Same-filesystem moves, retaining the label tree
without copying it, and removing recursive before/after byte scans reduced the
normalization phase to 12.729 seconds.

### Identity-worker scaling on the 18-image Plate

| Workers | Read-only verification time |
|---:|---:|
| 1 | 14.524s mean |
| 2 | 11.988s mean |
| 4 | 9.401s mean |
| 8 | 12.569s observed |
| 16 | 13.857s observed |
| 32 | 13.201s observed |

Four workers was fastest on the Windows development mount. This does not imply
that four is optimal for ACC storage or CPUs.

The successful ACC 18-image validation completed end to end in 4m03.21s. Its
result-import task took 1m15.41s, including 25.68s identity, 7.74s
normalization, 5.31s registration, and approximately 9.68s final bookkeeping.
The cisegmentation task took 2m11.32s. Because a separate full-result byte size
was not recorded, the 1,803,585-byte retained result does not establish an ACC
storage-reduction percentage.

## Large-Plate local baseline: September 8, 2026

The first detailed baseline was a detached `cisegmentation` workflow over Plate
2005. The later transfer reused a cached canonical snapshot; a separate cold
canonical identity attempt was observed earlier in the run.

| Item | Identifier |
|---|---|
| Detached launcher workflow | `0f7e18da-7756-4cdd-8f4c-80cd26416f41` |
| Analysis workflow | `6350afa4-7312-4138-8beb-47a1d60c6fd4` |
| Import task | `9f710675-a898-440a-af78-05eea415e30d` |
| Import order | `28a7449e-5d66-4be6-9662-250a8255414e` |

| Stage | Duration | Notes |
|---|---:|---|
| Separate cold canonical identity attempt | 9m02s | 846 nodes, four workers; not part of the later cached transfer |
| Input ZIP creation | 39m47.5s | Full input on Windows/Docker storage |
| Input SCP | 1m51.8s | |
| Remote unpack/no-op conversion | about 55.8s | Input was already Zarr |
| Analysis, Slurm job 574 | 4h47m19s | `c1`: 4 CPUs, 5 GiB, RTX 3060 allocation |
| Result ZIP creation | 2m08.8s | |
| Result SCP | 5m21.1s | |
| Permanent archive copy and extraction | 1h11m40.4s | Windows-backed `/data` mount |
| Recursive result discovery | 31m13s | Bug: descended through the complete outer Zarr; now fixed |
| Local returned-result identity | 42m55s | Four workers |
| Local normalization | 22m32.6s | |
| OMERO registration | 2m26s | |
| Importer order | 1h07m55s | Starts after the old recursive discovery phase |

The returned result was recorded as 8,968,870,301 bytes. Existing shallow
storage retained approximately 7-10% of full result storage, implying roughly
90-93% avoided storage. An exact retained-byte measurement for this particular
large run was not preserved. Applied to the later control's 8,349,150,014-byte
full result, that range would mean approximately 0.58-0.83 GB retained and
7.51-7.76 GB avoided; these are inferred values, not measured final sizes.

This run exposed three timing/correctness problems: the 31-minute recursive
discovery, a one-hour importer polling limit that expired 7m49s before import
completed, and an OMERO connection that had become stale during long local
preparation. These were subsequently addressed.

## Large-Plate local control: September 9-10, 2026

Workflow `1d531791-ec97-4d9a-b5c1-2f53d325e275` repeated the same 846-image,
four-label-per-image workflow with detached execution, pruned discovery,
adaptive importer polling, and importer keepalive enabled. Remote shallowing
was disabled; this used the embedded importer normalizer.

| Stage | Duration | Result |
|---|---:|---|
| Cached canonical discovery | 0.367s | All 846 identities found |
| Input ZIP creation | 38m36.8s | 6,465,013,963-byte ZIP |
| Input SCP | 1m40.9s | |
| Remote unpack/no-op conversion | 54.2s | |
| Slurm analysis, job 575 | 4h38m10s | `c1`: 4 CPUs, 5 GiB, RTX 3060 allocation |
| Result ZIP creation | 2m04.4s | 7,334,711,660 B from 8,349,150,014 B |
| Result SCP and validation | 4m55.7s | |
| Permanent copy and extraction | 1h11m34.5s | |
| Recovery result discovery | about 0.15s | Previously 31m13s |
| Local returned-result identity | 41m59.5s | Eligible: `input-plate-unchanged` |
| Local normalization | 21m27.4s | 846 image nodes stored shallow |
| OMERO registration | 2m23.7s | Plate 2151 in Screen 301 |
| Importer terminal detection | 29.8s | Adaptive poll |
| Metadata/workflow finalization | 3.0s | Final `DONE`, 100% |
| Successful recovery import order | 1h05m52.5s | Reused staged results |
| Complete observed workflow | 8h08m33.2s | Includes about 22m33s operator repair/recovery delay |
| **Clean stage-sum expectation** | **about 7h44m** | Excludes the operator delay and failed first import invocation |

The first import-script invocation failed immediately after the 71-minute
extraction because the OMERO script-client connection had expired. The durable
staged result allowed recovery without repeating analysis, transfer, or
extraction. The script-client keepalive was added after this observation.

### Analysis breakdown

| Analysis component | Duration |
|---|---:|
| Cellpose `cyto3` | 1h11m41s |
| Cellpose `nuclei` | 2h32m57s |
| Spotiflow `general` | 12m23s |
| Label finalization | 12m55s |
| DuckDB measurements | 25m00s |
| Database merge | 2m33s |

## ACC prerelease attempts before the next comparison

### Small 18-image Plate: relative alias defect

The small ACC Plate exercised a cold Plate canonical-promotion path. The source
Zarr existed, but an individual nested node such as `A/1/0` was presented to
ISCC-BIO through a temporary `.ome.zarr` symlink whose relative target was
interpreted from `/tmp`. The link was therefore broken.

This was a correctness failure, not a performance result. It did not establish
successful canonical promotion, return-side shallow timing, or storage saved.
The defect was in BIOMERO.importer 1.5.0b4 and was fixed by resolving the target
strictly to an absolute path before creating the temporary alias. The same fix
was subsequently carried into the standalone shallower implementation, where
the identity code now lives on the remote-shallower branch.

### Plate 252: input-preparation connection failure

This attempt used all 846 source images and NL-BIOMERO v1.8.0-beta.4.
The workflow identifier was `59dfba93-6926-4d09-bac1-5219850055a3`.

| Stage | ACC observation | Closest local observation | Comparison note |
|---|---:|---:|---|
| OMERO Plate export | 54m53s | No separated local export value | New useful baseline |
| Cold source canonical identity | about 55s | 9m02s cold local attempt | Same broad phase; different deployment/data state |
| Input ZIP creation | about 3m38s | 38m36.8s | ACC was about 35 minutes faster |
| Transfer and unpack | about 2m47s | 2m35.1s | Similar |
| Segmentation | Not reached | 4h38m10s | Not comparable |
| Result return/import/shallowing | Not reached | See local control | Not comparable |
| Attempt total | 1h03m35s, failed | About 7h44m clean complete expectation | Not comparable |

The canonical calculations finished, but the canonical snapshot could not be
committed after the long export because the original OMERO script connection
was no longer usable. The fallback ZIP was nevertheless created, transferred,
and unpacked. Writing final script outputs then raised
`Ice.ConnectionLostException`, so the detached supervisor received no message
and marked the workflow failed at 5%.

No retry was submitted. The failed attempt left approximately 6.4 GiB and
27,294 files in its remote scratch directory; these are failed-attempt staging
figures, not a completed analysis-result size.

The previously circulated comparison of the ACC 55-second source identity pass
with the local 41m59.5s returned-result identity phase is invalid: those are
different lifecycle stages. The relevant local source-side comparisons are the
9m02s cold attempt and the 0.367s cached lookup, with their cache states stated.

## ACC successful full integration: September 15, 2026

The `A-FULL-HCS` run completed the detached, shallow-Zarr workflow over all 846
source Images. It ran one Cellpose `cyto3` model pass and returned one label
set per Image. It used NL-BIOMERO `1.8.0-beta.5`, BIOMERO `2.9.0b6`,
biomero-scripts `v2.9.0-beta.6`, BIOMERO.importer `1.5.0-beta.5`, and
OMERO.biomero `1.7.0b3`.

| Item | Identifier or value |
|---|---|
| Workflow | `d5a15987-88c6-41bf-8930-6c23a6b56d81` |
| Slurm job | `3296955` |
| Result | Plate 402 |
| Input and result Images | 846 source Images; 846 unique result Images |
| Analysis payload | Cellpose `cyto3` only; 846 model-field segmentations |
| Inference concurrency | Eight configured and effective workers |
| Slurm allocation | `gpu02`, `gpu` partition, 18 CPUs, 64 GB, one `1g.12gb` MIG allocation, 24-hour limit |
| GPU evidence | Exact profile `1g.12gb`; parent GPU and host driver unavailable after allocation ended |
| Final state | `DONE`, 100%, main task `cisegmentation` |

### End-to-end timing

| Stage | ACC | Windows/Docker control | Comparison note |
|---|---:|---:|---|
| Input ZIP creation | 16m04.667s | 38m36.8s | ACC faster |
| Input SCP | 1m15.412s | 1m40.9s | ACC faster |
| Remote unpack/no-op conversion | 1m09.740s | 54.2s | ACC 15.5s slower |
| Complete input transfer task | 18m30.217s | About 41m12s | ACC about 2.23x faster |
| Slurm cisegmentation | 25m37s | 4h38m10s | Different analysis payloads; not a hardware speedup |
| Result ZIP creation | 3m47.614s | 2m04.4s | ACC slower despite producing fewer labels |
| Result SCP and validation | 1m20.952s | 4m55.7s | ACC faster |
| Result ZIP plus transfer | 5m08.566s | About 7m00s | ACC about 1.36x faster |
| Permanent copy and extraction | 25m27.007s | 1h11m34.5s | ACC about 2.81x faster |
| Result discovery | About 0.004s | About 0.15s | Both effectively immediate |
| Identity and eligibility | 23m58.848s | 41m59.5s | ACC about 1.75x faster |
| Shallow normalization | 21m09.428s | 21m27.4s | Nearly identical |
| OMERO registration/reference attachment | About 1m13.153s | 2m23.7s | ACC faster |
| Workflow final bookkeeping | About 1m51.868s | Not directly separated | |
| Registration through `DONE` | About 3m05s | About 2m57s | Similar |
| **End to end** | **2h04m27.654s** | **About 7h44m** | Operational scenarios differ; raw ratio about 3.73x |

The ACC input reused the canonical Plate cache. The Windows control also used
a cached canonical discovery before packaging, but the two deployments have
different storage and export paths; neither timing should be presented as an
ACC-versus-Windows cold-export comparison.

The end-to-end and Slurm ratios are useful operational observations, not
like-for-like analysis benchmarks. ACC performed one model pass and produced
846 label sets, whereas Windows performed three model passes and produced
3,384 label sets plus substantially more objects, intensity rows, and
relationships.

### Analysis concurrency and throughput

The exact ACC image was workflow `cisegmentation v0.5.0`, built from
`cellularimagingcf/w_cisegmentation:v0.5.0` as this SIF:

| Image property | ACC value |
|---|---|
| SIF path | `/appdata/users/svc_omero_acc/singularity_images/workflows/cisegmentation/w_cisegmentation_v0.5.0.sif` |
| SHA-256 | `c15d9c7a8427711107a4416e96a2c2f06e8e543860ca84980a724fef3f342105` |
| SIF size | 8,095,285,248 B |
| Build | Apptainer 1.4.1-1.el9, amd64, 2026-09-01 16:59:38 CEST |
| Runtime | Python 3.11.15; PyTorch 2.11.0+cu126; Cellpose 4.2.1.1; legacy Cellpose 3 1.1.0; Spotiflow 0.6.5 |

The effective analysis selected Cellpose `cyto3` on channel 1 with nuclei
channel 0, cell expansion 10.0, border-cell removal, original-data inclusion,
label overwrite, DuckDB measurements, automatic CUDA device selection, eight
inference workers, and automatic label/measurement workers. The nucleus and
all four foci models were `skip`; batching and ROI conversion were disabled.

The cached input was Plate 252, `20220714_TKI_482`, at
`.accprocessed/Plate-252.g1.ome.zarr`: 846 two-channel `uint16` fields with
shape `[2, 2008, 2008]` and axes `[c, y, x]`. All 846 canonical identities
were cached. An example recorded identifier was
`ISCC:K4AFSE7HLJIVOJZDI3NNBUZRSOU7S4IORQQHEVURCTDPRHM3EJLU22I`. No matching
Windows canonical identifier was retained, so byte-identical input across the
deployments is not established.

The Windows command used the same `v0.5.0` workflow tag, but its retained SIF
digest is unavailable. Both implementations sum the time around each model
evaluation into the aggregate `inference` metric. The workloads themselves
were nevertheless different:

| Evidence | ACC job 3296955 | Windows/Docker job 575 | Interpretation |
|---|---:|---:|---|
| Models executed | Cellpose `cyto3` | Cellpose `cyto3`, Cellpose `nuclei`, Spotiflow `general` | Windows ran three passes instead of one |
| Model-field segmentations | 846 | 2,538 | Exactly 3x as many on Windows |
| Aggregate inference | 9,932.66s | 90,500.42s | Raw 9.111x ratio combines workload count and speed |
| Average per model-field segmentation | 11.742s | 35.658s across the mixed models | Mixed-model average is 3.037x higher on Windows |
| Inference wall time | 21m18s | 3h57m01s across all three passes | Not a like-for-like wall comparison |
| Effective workers | 8 | cyto3: 6; nuclei: 7; Spotiflow: 2 | Windows was not serial |
| Inference retries | 0 | 0 for every model pass | Retry/OOM behavior does not explain the difference |
| GPU | `1g.12gb` MIG allocation; parent unknown | RTX 3060, 12 GB | Equal VRAM does not establish equal compute |

The 9.111x aggregate difference decomposes almost exactly into three times as
many model-field executions and a 3.037x difference in the mixed per-execution
average. It must not be reported as a 9.11x GPU speedup. Even the 3.037x figure
is not a direct GPU comparison because the Windows average includes three
different models.

The closest individual comparison is Cellpose `cyto3`: ACC completed it in
21m18s with eight workers, while Windows completed it in 1h11m41s with six
workers, a 3.36x wall-time difference. The Cellpose channel parameters were not
identical (`cell_nuclei_channel=0` on ACC versus `1` on Windows), and exact
source-pixel identity is unproven, so this also remains an operational rather
than controlled benchmark.

Dividing ACC's 9,932.66 aggregate inference-seconds by eight predicts about
20m42s, close to its observed 21m18s and consistent with good worker
utilization. ACC then used 17 label workers and 17 measurement workers without
retries. It produced 846 label sets, 316,656 objects, 633,312 intensity rows,
and no relationships. Windows produced 3,384 label sets, 1,029,249 objects,
2,058,498 intensity rows, and 1,420,786 relationships.

### ACC analysis timing detail

| Analysis component | ACC observation |
|---|---:|
| Cellpose `cyto3` inference wall time | 21m18s |
| Segmentation aggregate runtime | 9,993.94s |
| Aggregate inference | 9,932.66s |
| OME-Zarr reads | 30.65s |
| Imports | 50.62s |
| Model loading | 9.35s |
| Label finalization wall time | 8.79s |
| OME-Zarr label writes | 59.23s |
| Complete measurement phase | 151.11s |
| Source publication copy | About 1m23s; 6,328.1 MiB and 27,295 files |
| Actual cisegmentation process wall time | 25m32.69s |
| Slurm elapsed | 25m37s |

### Storage and shallow-manifest evidence

| Measurement | ACC value |
|---|---:|
| Full returned archive entries before shallowing | 53,522 |
| Full returned Zarr bytes | Unavailable |
| Retained shallow tree | 540,582,567 B; 28,141 regular files |
| Canonical generation-1 Zarr reused | 6,635,474,968 B; 27,295 regular files |
| `.biomero-shallow.json` | 2,959,587 B |
| Manifest mappings | 846 returned Images, 846 canonical sources, 846 label nodes/components |

The manifest records schema 1, model `rfc8-shallow-copy`, and interchange
profile `ngff-0.4-zarr-v2`. It contains one `labels_cells` output for each
Image. The pre-shallow byte count and deleted result-ZIP size are unavailable,
so neither exact avoided bytes nor an ACC storage-reduction percentage can be
calculated. The canonical tree size must not be substituted for the missing
full returned-result size.

### Exact ACC timeline

All timestamps are CEST, UTC+02:00.

| Event | Timestamp |
|---|---|
| Workflow initiated | 2026-09-15 11:16:45.395 |
| Input packaging began | 11:17:04.504 |
| Canonical inputs recorded | 11:35:48.127 |
| Slurm start / end | 11:36:12 / 12:01:49 |
| Result ZIP began | 12:02:18.395 |
| Retrieval/extraction completed | 12:32:53.970 |
| Import order started | 12:32:56.069 |
| Identity evaluation started | 12:32:59.555 |
| Shallow eligibility established | 12:56:58.403 |
| Shallow normalization completed | 13:18:07.866 |
| Registration began | 13:18:07.867 |
| Plate reference attached | 13:19:21.020 |
| Import completed event | 13:19:21.181 |
| Provenance annotation failure | 13:20:14.812 |
| Result marked imported | 13:20:15.346 |
| Importer workflow completed | 13:20:54.535 |
| Final workflow `DONE` | 13:21:13.049 |

### Correctness and detached-session verification

- The originating browser/OMERO session expired after ten minutes while the
  detached workflow continued to completion.
- Plate 402 contains exactly 846 unique result Images; the source Plate's 846
  Image IDs remained unchanged.
- `CanonicalInputsRecorded` is present and `.biomero-shallow.json` contains 846
  Image and label mappings.
- No CUDA retry or OOM, timeout, stale connection, duplicate Slurm job, or
  duplicate import was observed.
- The internal orchestration launcher remained `CLAIMED`, but that mechanical
  state did not leak into the analysis or workflow-facing status.

One post-success task-provenance `MapAnnotation` transaction failed. The
offending key was
`Task_SLURM_Run_Workflow.py_Param_wf_params_cisegmentation` in namespace
`biomero/workflow/task/SLURM_Run_Workflow.py`. Its value contained the complete
serialized 9,455-character workflow-parameter schema rather than only the
effective values. The 9,458-byte UTF-8 value produced a 2,800-byte PostgreSQL
B-tree index row, exceeding the 2,704-byte limit.

The workflow-level provenance annotation, importer annotation, shallow
annotation, canonical linkage, and attached Slurm log succeeded. The failed
transaction rolled back attempted annotation 978, and the annotation loop did
not write subsequent task/job provenance entries. This known metadata defect
did not affect result pixels, the shallow manifest, exact-once registration, or
the final workflow state. It should be tracked separately from the successful
detached/shallow-Zarr integration test.

## ACC three-model integration: reported September 16, 2026

This successful run used normal local importer shallowing. Remote shallowing
was not enabled. It executed the same three model types and the same number of
model-field segmentations as Windows job 575. The datasets are equivalent in
shape and workload, but byte-identical source pixels are not established.

| Item | ACC evidence |
|---|---|
| Workflow | `3999cf8b-6eac-4cab-84b0-0bd4fe826d91` |
| Slurm job | `3297294` |
| Source | Plate 252, all 846 Images |
| Result | Plate 403, imported exactly once |
| Final status | `DONE`, 100%; main task remained `cisegmentation` |
| Session lifecycle | Detached execution continued after the OMERO.web session expired |
| Canonical recording | `CanonicalInputsRecorded`, database event row 1553 |
| Labels | 3,384 label sets, exactly four per source Image |
| End-to-end duration | 3h39m36s |

The detailed ACC evidence is retained remotely at
`/data/biomero/omero/deploy-omero/logs/integration-tests/2026-09-15-cisegmentation-windows-equivalent.md`.
The integration runbook profile starts at line 844; its update was reported as
uncommitted. Exact run timestamps, component versions, SIF digest, complete
parameters, and reduction-trigger messages were not included in the supplied
summary and must not be copied from the preceding one-model run by assumption.

### Model execution and comparison

| Model | ACC effective workers | ACC wall time | Windows workers / wall time | ACC throughput | ACC GPU / RSS probe | ACC worker adjustment |
|---|---:|---:|---|---:|---|---|
| Cellpose `cyto3` | 8 | 23m21s | 6 / 1h11m41s | 0.60 fields/s | 906 MiB / 1,925.8 MiB | 17 to 8 |
| Cellpose `nuclei` | 8 | 23m35s | 7 / 2h32m57s | 0.60 fields/s | 906 MiB / 1,908.5 MiB | 17 to 8 |
| Spotiflow `general` | 3 | 6m25s | 2 / 12m23s | 2.19 fields/s | 2,576 MiB / 1,446.1 MiB | 6 to 3 |

The ACC controller performed two automatic worker reductions for each pass.
No CUDA OOM was logged. Worker reductions must not be described as OOMs or
ignored as though this were a fixed-worker benchmark; their triggers and
intermediate worker counts require the detailed controller messages.

| Aggregate evidence | ACC | Windows job 575 | Interpretation |
|---|---:|---:|---|
| Model-field segmentations | 2,538 | 2,538 | Same execution count and model mix |
| Aggregate inference | 20,864.76s | 90,500.42s | ACC 4.34x lower on the reported metric |
| Average inference per model-field | 8.221s | 35.658s | Same 4.34x aggregate-average ratio |
| Combined model-pass wall time | 53m21s | 3h57m01s | Raw ratio 4.44x; worker counts differ |
| Full Slurm job | 1h03m30s | 4h38m10s | Raw ratio 4.38x |

This replaces the one-model run's misleading 9.11x aggregate comparison with
a same-count, same-model-mix observation. It is still not an isolated GPU
hardware benchmark: source identity, exact parameters/builds, allocation, and
effective concurrency must also match. Per-model aggregate inference seconds
were not emitted, so the aggregate speed ratio cannot be assigned to an
individual model.

### Integration timing

| Stage | ACC three-model run | Windows/Docker control | Interpretation |
|---|---:|---:|---|
| Input preparation and transfer | 14m40s | About 41m12s | Both reused canonical input; includes packaging/transfer, not a measured cold export |
| Slurm workflow | 1h03m30s | 4h38m10s | ACC substantially faster |
| Result ZIP plus SCP | 7m30s | About 7m00s | Similar combined duration |
| Permanent copy/extraction block | 38m44s | 1h11m34.5s | Separate copy and extraction boundaries unavailable in summary |
| Identity calculation | 1h03m48s | 41m59.5s | ACC 21m48.5s slower |
| Shallow normalization | 27m29s | 21m27.4s | ACC 6m01.6s slower |
| Identity plus normalization | 1h31m17s | 1h03m26.9s | ACC about 1.44x longer |
| OMERO registration | 1m11s | 2m23.7s | Registration only; do not compare with Windows registration-through-DONE |
| **End to end** | **3h39m36s** | **About 7h44m** | **About 2.11x faster / 52.7% lower elapsed time** |

The 4h04m24s end-to-end saving is relative to the rounded, uninterrupted
Windows expectation, not its observed 8h08m33s recovery run. The supplied
stage durations are rounded and do not form a complete exact timeline.

Identity plus normalization accounts for about 41.6% of ACC's elapsed time.
The faster analysis, input path, and extraction outweighed the slower shallow
processing. Similar source counts alone do not identify the cause of that
identity/normalization difference; input layout, label tree, storage, cache
state, and effective identity workers need comparison.

### Measured storage reduction

| Measurement | ACC three-model result |
|---|---:|
| Result ZIP | 7,521,358,997 B |
| Full returned Zarr | 7,902,699,055 B; 127,123 files |
| Retained shallow Zarr | 1,271,758,230 B; 101,743 files |
| Avoided bytes | 6,630,940,825 B (83.91%) |
| Avoided files | 25,380 (19.96%) |
| Canonical source reused | 27,295 files; approximately 6.18 GiB |
| `.biomero-shallow.json` | 5,906,988 B |
| Manifest mappings | 846 Images; 3,384 label components and paths |

This is the first exact full-screen ACC reduction measurement in this ledger.
It supersedes neither the small-result 92.6% measurement nor the inferred
Windows full-screen 90-93% range: those are different returned payloads and
evidence levels. Avoided pixel bytes are large while the file-count reduction
is only about 20%, because the returned label trees remain stored.

### Small-run scale context supplied alongside this result

The new ACC summary also reports an 18-image cyto3-only observation: 1m03s
input preparation, 1m41s Slurm runtime, 25.62s identity, 7.74s normalization,
4.58s registration, and approximately 4m03s end to end. These component times
differ from the earlier small-run record. No workflow identifier was supplied
to establish whether this is another run or revised phase accounting, so keep
it as a separate reported observation rather than overwrite the earlier
2m11.32s analysis and 25.68s identity measurements.

## Archive and extraction microbenchmarks

### Full retained 846-image result

The tested full result contained 8,968,870,301 bytes and 269,366 filesystem
entries.

| Format | Create time | Archive size | Observation |
|---|---:|---:|---|
| ZIP, default Deflate | 132.306s | 7,696,668,188 B | Current return-transfer baseline |
| ZIP, store only | 89.597s | 9,039,309,435 B | Transfer growth outweighed packing gain at observed network rate |
| ZIP, maximum Deflate | More than 453s | Not retained | Stopped after exceeding break-even time |
| tar + Zstandard level 3 | 26.381s | 7,686,925,055 B | About 106s faster than ZIP; only 9.7 MB/0.13% smaller |

The full tar.zst extracted onto the Windows-backed `/data` mount in 39m16.8s.
That is not a direct comparison with the 1h11m40s ZIP block because the latter
also included a separate permanent-storage archive copy.

### 18-image extraction comparison on the same local mount

| Extractor/input | Duration |
|---|---:|
| Python `zipfile`, cold | 85.16s |
| Python `zipfile`, warm | 85.46s |
| Info-ZIP `unzip` | 201.94s |
| 7-Zip | 213.13s |
| Python `zipfile`, store-only ZIP | 72.75s |
| tar.zst extraction | 95.14s |

Replacing Python extraction with Info-ZIP or 7-Zip was not beneficial on this
deployment. Zstandard's useful result was much faster archive creation on the
large result, not a meaningfully smaller transfer.

## Standalone shallower correctness smoke

The mounted container fixture used real ISCC-BIO hashing, retained label pixels,
removed duplicate image arrays, preserved canonical bytes, and reused its
report on rerun.

| Stage | Duration |
|---|---:|
| Identity evaluation | 3.5411s |
| Normalization | 0.1099s |

This is a small-fixture correctness measurement. It does not predict the
standalone helper's 846-image Slurm performance.

## Changes that affect comparisons

| Change | Expected or observed effect |
|---|---|
| Stop discovery at the first outer `.zarr` | Observed reduction from 31m13s to about 0.15s |
| Adaptive import polling up to 24 hours | Prevents false one-hour timeout; terminal detection observed at 29.8s |
| OMERO gateway and script-client keepalive | Intended to preserve outputs/metadata across long preparation stages |
| Resolve Plate identity aliases to absolute paths | Fixes cold Plate canonical promotion from relative working paths |
| Four local identity workers | Fastest measured setting on the Windows development mount |
| Detached workflow supervisor | Removes dependency on the browser/web session; not itself a shallowing speed optimization |
| Optional remote shallower | Intended to normalize before result archiving; no full-screen performance result yet |

## Missing data and next ACC record

The following measurements are not yet available or were not preserved:

- the pre-shallow full-result bytes and exact avoided percentage for the
  ACC one-model 846-image run (now measured for the three-model run);
- the pre-shallow bytes for the successful ACC 18-image run;
- a cold source identity run and a warm cached rerun on the same ACC Plate;
- a full-screen run with remote shallowing enabled;
- separate permanent archive-copy and extraction times on both Windows and
  ACC;
- remote shallower identity, normalization, archive size/time, SCP, extraction,
  receipt validation, and OMERO registration durations;
- the ACC parent GPU model, host driver, and host-supported CUDA version;
- the exact Windows cisegmentation SIF digest and a canonical source identity
  or checksum comparable with ACC Plate 252;
- a controlled ACC-versus-Windows Cellpose `cyto3` run with identical source
  pixels, channels, parameters, worker limit, and container digest;
- the deleted ACC one-model result-ZIP byte size;
- the three-model ACC run's component versions, exact image digest,
  allocation, full parameters, identity-worker configuration, and controller
  reduction triggers from its detailed remote report.

Append new ACC measurements using this shape:

| Field | Value |
|---|---|
| Date, deployment and component versions | |
| Workflow, task, Slurm job, Plate and Screen identifiers | |
| Images, wells, labels and result entries | |
| Canonical state: cold, cached or fallback | |
| OMERO export | |
| Source identity and canonical commit | |
| Input archive creation, bytes, transfer and unpack | |
| Analysis and per-tool breakdown | |
| Remote normalization, if enabled | |
| Result archive creation, bytes and transfer | |
| Local copy and extraction | |
| Result discovery | |
| Local identity and normalization, if used | |
| OMERO registration and finalization | |
| Full bytes, retained bytes and percentage avoided | |
| Final workflow status and total elapsed time | |
| Errors, retries, cache reuse or operator delays | |
