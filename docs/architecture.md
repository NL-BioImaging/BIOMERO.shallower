# Architecture, transactions, and recovery

`biomero-schema` owns only portable contracts. `biomero-shallower` owns NGFF
discovery, ISCC-BIO filesystem identity and move-journal normalization.
`biomero-importer.utils.result_zarr` exports the shared implementation for
existing callers; its identity subclass keeps the OMERO reader adapter in the
importer. Registration planning and managed-source resolution retain their
established semantics.

`biomero` acquires a versioned SIF through its image runner and starts a CPU job.
`biomero-scripts` selects the stage only when shallow storage and compatible
importer capabilities are enabled. Within that enabled feature, remote
normalization is preferred unless the administrator selects the local path
with `BIOMERO_REMOTE_SHALLOW_ZARR=false`. It runs before ZIP creation. The
import order carries receipts read from completed event-sourced normalizer
tasks, rather than trusting a batch file found in a workflow archive.

## Transaction

1. Acquire a kernel file lock for the returned store; recover any earlier intent.
2. Discover nodes and generate exact image and label identities using the
   existing ISCC-BIO/Imagewalk adapter and semantic guards.
3. Save original image attributes and every planned move in a durable sibling
   `.STORE.biomero-prune-TOKEN/journal.json` before moving arrays.
4. Move duplicate image arrays and inherited label trees into that sibling
   journal on the same filesystem. Retained trees are never copied.
5. Write shallow metadata and the operation report, and validate the complete
   reference inventory and retained label structure.
6. Persist the committed journal state, then unlink verified duplicates.

An exception before commit restores arrays and original attributes. A killed
process leaves durable intent: on resume, uncommitted intent is rolled back;
committed intent only finishes cleanup. A valid committed report can be reused
without generating identities again. The canonical store is never mutated or
mounted by the Slurm helper. Returned symlink trees are rejected.

The completed workflow must have stopped writing its output before this stage.
POSIX locks, atomic rename, and fsync on the mounted filesystem are required.
The helper does not claim durability on filesystems that ignore these primitives.
Never remove a prune journal to free space: it may contain the only remaining
copy of a pre-commit array.

## Slurm events and detached resume

The `_SLURM_Result_Normalizer` task records its configured image/version and
input directory. TaskCreated/TaskAdded/TaskStarted precede submission;
JobIdAdded is saved immediately after the remote submission ledger returns.
TaskCompleted stores the validated batch report. Workflow progress projections
ignore this internal task; analytics still retain its provenance.

The remote submission ledger sits outside archived output under
`.biomero-normalizer/TASK/`. `flock` serializes submission. A saved job ID is
adopted. An intent without an ID is reconciled through the unique task job name
in `sacct`; an empty or ambiguous accounting result blocks retrieval rather
than submitting another job. Retry once accounting is available. Operators can
reconcile a proven job ID into the ledger if accounting retention has expired.

A valid batch report is adopted even if the orchestration process died before
recording TaskCompleted. A failed helper launches a CPU recovery job with the
same image: interrupted stores roll back and completed stores retain receipts.
Only after recovery completes can the mixed full/shallow output be archived.
Unresolved submission, unknown scheduler state, or failed rollback preserves
the output and stops retrieval for safe operator recovery.

Image acquisition failures precede all result mutation and safely fall back to
the existing full-result transfer. The only supported policy is `keep-full`;
there is no new strict scientific-result policy.

## Trust and portability

Managed storage root IDs, relative paths, generations, and canonical pixel
identities travel unchanged from workflow input tracking. OMERO-side registration
resolves them using its authoritative local storage mappings. Slurm paths are
only operational job parameters, never portable source references.

The importer requires administrator enablement and matching configured image
and package versions. It checks the receipt's report SHA-256, canonical snapshot,
task/job, manifest equality, reference membership, and retained structure. User
renaming after transfer can bind through the unchanged report checksum. It does
not hash pixel chunks again. SHA-256 here binds small metadata; it is not a
cryptographic signature or a pixel integrity scan.

This assumes trusted BIOMERO orchestration, its event store and import-order
writers, configured container images, and the Slurm account/filesystem. Workflows
must not forge reserved `.biomero-*` metadata. It is not an attestation protocol
against a malicious workflow sharing that account. Deployments requiring that
threat model need isolated ownership and signed receipts before enabling it.

The report formats and receipt compatibility rules are documented in
[BIOMERO Schema](https://nl-bioimaging.github.io/biomero-schema/remote-shallower-contracts/).
For deployment flags and image initialization, use the
[NL-BIOMERO administration guide](https://nl-bioimaging.github.io/NL-BIOMERO/master/sysadmin/remote-shallower.html).

The archive extension point is after normalizer completion and before
`zip_data_on_slurm_server`; a later archive adapter can replace ZIP independently.

## Adding a contract adapter

Add explicit versioned models to `biomero-schema`, then a filesystem adapter
with independent parity, rollback, and import-validation tests. Dispatch it from
`operations.ADAPTERS` and expose its accepted version in CLI choices. Shallow
manifest schema 2 represents scientific image/label relationships separately
from managed-storage bindings. The current writer still targets NGFF 0.4 / Zarr
v2 and does not claim RFC-8 compliance; a future adapter can project the graph
once an accepted collections profile and compatible stores are available.

## Python API

These filesystem interfaces are shared with BIOMERO.importer. They do not
accept OMERO connections.

::: biomero_shallower.operations
    options:
      members:
        - normalize
        - validate_report
