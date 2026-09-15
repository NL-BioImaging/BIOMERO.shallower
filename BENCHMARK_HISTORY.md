# BIOMERO shallow-Zarr benchmark history

Last updated: 2026-09-15

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

## Executive summary

| Observation | Dataset | Storage result | Shallow-specific processing | End-to-end status |
|---|---|---:|---:|---|
| Local production-path benchmark | 18 images, one new label each | 92.6% saved | about 25.4s | Isolated importer benchmark |
| Local five-Image live batch | Five Images with new and inherited labels | 91.9% saved | about 10s | Successful |
| Local multi-generation Image | One Image with five inherited and four new labels | 88.9% saved | 19.7s | Successful |
| Windows/Docker large-Plate control | 846 images, four labels each | Approximately 90-93% inferred saved | 1h03m26.9s | Successful after recoverable connection failure |
| ACC Plate 252 attempt | 846 source images | Not reached | Return-side shallowing not reached | Failed after 1h03m35s during input preparation |
| ACC small Plate prerelease | 18 source images | Not established | Not reached successfully | Failed during cold canonical identity aliasing |

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
- The recorded attempt failed during input preparation and did not reach
  segmentation or return-side normalization.

## Small-result storage and processing measurements

| Scenario | Full or estimated full | Stored shallow | Avoided | Processing or reconstruction |
|---|---:|---:|---:|---:|
| 18-image Plate | 146,143,912 B | 10,775,929 B | 135,367,983 B (92.6%) | 12.653s identity mean + 12.729s normalization mean = about 25.4s |
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
| Analysis | about 4h48m | GPU workflow |
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
| Slurm analysis, job 575 | 4h38m10s | Completed |
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

- an exact successful end-to-end duration for the small 18-image Plate;
- exact retained bytes for the successful 846-image shallow result;
- a clean 846-image run without any operator recovery delay;
- a cold source identity run and a warm cached rerun on the same ACC Plate;
- a completed ACC segmentation and return path with the current keepalive and
  alias fixes;
- a full-screen run with remote shallowing enabled;
- separate permanent archive-copy and extraction times on Windows;
- remote shallower identity, normalization, archive size/time, SCP, extraction,
  receipt validation, and OMERO registration durations.

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
