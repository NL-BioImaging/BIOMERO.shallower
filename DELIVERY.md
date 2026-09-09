# Remote Shallower delivery — 2026-09-09

Implemented locally on the existing feature branches. No branches were switched,
no changes were pushed or merged, and no running deployment service was rebuilt
or restarted. No real Slurm workflow was launched.

## Artifacts

- Source package: `D:\workspace\biomero-shallower`, version `0.1.0`.
- Wheels: `dist/biomero_shallower-0.1.0-py3-none-any.whl` and
  `dist/biomero_schema-0.2.1.dev1-py3-none-any.whl`.
- Local image: `biomero-shallower:0.1.0`.
- Image ID: `sha256:17768efc15eeded2ab22d40ca3fc5a01bc2f7da6a040aa574df4c237b822f952`.
- Image size: 663,961,485 bytes; runtime user `10001:10001`.
- This image ID identifies the local Docker image. It is not a published OCI
  registry manifest digest; obtain that digest when publishing for cluster use.

## Commits

| Repository | Branch | Implementation commits |
|---|---|---|
| biomero-shallower | master (new local repository) | `bd2cc6f`, `e80be63`, `085e871` |
| biomero-schema | feature/zarr-shallow-storage | `cb966f7`, `c30566c` |
| NL-BIOMERO/biomero-importer | fix/preprocessing-connection-keepalive | `f0e5e38`, `27f43d5` |
| biomero | session-poller | `28181b2` |
| biomero-scripts | session-poller | `e151b9d` |
| biomero-scripts-test-suite | test-suite | `dcff750` |
| NL-BIOMERO | session-poller | `8f38dadc` |

The schema's existing `.gitignore`, `pixi.lock`, and `pyproject.toml` edits remain
unstaged. NL-BIOMERO's existing `.env` and deployment-ini edits also remain
unstaged. Only the new ini comments were staged for its configuration commit.
The nested importer checkout remains an unstaged submodule-pointer difference,
as it was at the start; its implementation commits are listed above.

## Verification

**809 tests passed across the relevant suites.** These are suite counts, not
claims of 809 independent new tests; the standalone suite retains original
importer parity fixtures.

| Suite | Result |
|---|---:|
| BIOMERO complete `tests/unit` | 435 passed |
| Importer complete `tests/unittests` | 155 passed |
| Scripts complete shared harness against feature source | 115 passed |
| Schema complete `tests` | 67 passed |
| Standalone complete `tests` on Linux | 37 passed |

New-feature red runs demonstrated missing standalone/remote APIs and rejection
tests failing before implementation. Existing Image/Plate parity assertions
cover inherited, unchanged, new and changed labels, ambiguous canonical matches,
passthrough, rollback and materialization. Added tests cover durable interrupted
transactions, post-commit cleanup recovery, report tampering, administrator
enablement, renamed results, fallback followed by local import/retry, version
rejection, no production byte scans, job adoption and progress isolation.

Exact principal commands, from `D:\workspace` unless a working directory is
shown (PowerShell):

```powershell
# Core: cwd D:\workspace\biomero
$env:PYTHONPATH='D:\workspace\biomero-schema\src'
.\venvTest\Scripts\python.exe -m pytest tests/unit -q

# Schema: cwd D:\workspace\biomero-schema
$env:PYTHONPATH='D:\workspace\biomero-schema\src'
D:/workspace/NL-BIOMERO/biomero-importer/.venv/Scripts/python.exe -m pytest tests -q

# Scripts harness: cwd D:\workspace\biomero-scripts-test-suite
$env:BIOMERO_SCRIPTS_ROOT='D:\workspace\biomero-scripts'
$env:BIOMERO_TEST_REMOTE_SHALLOWER='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONPATH='D:\workspace\biomero-schema\src'
D:/workspace/.tmp-biomero-scripts-ci-venv/Scripts/python.exe -m pytest tests -q

# Scripts compilation: cwd D:\workspace\biomero-scripts
D:/workspace/.tmp-biomero-scripts-ci-venv/Scripts/python.exe -m compileall -q admin _data __workflows
git diff --check

# Standalone suite: cwd D:\workspace
docker run --rm --network none --volume D:/workspace/biomero-shallower:/shallower:ro --volume D:/workspace/biomero-schema:/schema:ro --env PYTHONPATH=/shallower:/schema/src --workdir /shallower --entrypoint /opt/conda/bin/conda nl-biomero-biomero-importer:latest run --no-capture-output -n auto-import-env python -m pytest tests -q -o cache_dir=/tmp/pytest-cache

# Full importer suite, coverage and both CI lint passes, in a disposable container
docker run --rm --volume D:/workspace/NL-BIOMERO/biomero-importer:/work --volume D:/workspace/biomero-shallower:/shallower:ro --volume D:/workspace/biomero-schema:/schema:ro --env PYTHONPATH=/shallower:/schema/src --workdir /work --entrypoint /opt/conda/bin/conda nl-biomero-biomero-importer:latest run --no-capture-output -n auto-import-env sh /shallower/tools/importer_ci.sh
```

The importer CI script runs `pytest --cov=biomero_importer
--cov-report=term-missing`; aggregate importer coverage is **38%**. It also runs
the blocking `flake8 --select=E9,F63,F7,F82` check (**0 errors**) and the existing
advisory style/complexity check. Full output is retained at
`D:\workspace\.verification\importer-final.log`.

Native Windows importer imports stalled; its documented disposable Linux
fallback completed. Windows standalone materialization intermittently encountered
directory-lock `WinError 5`; the complete Linux suite passed. The original thread
test incorrectly assumed two successive worker pools reused the same thread IDs;
its assertion now permits distinct pools while preserving concurrency and order
checks. The core's existing network-dependent invalid-URL test needed execution
outside the blocked network sandbox.

Both documentation builds completed:

- Core: `venvTest/Scripts/python.exe -m sphinx -b html docs docs/_build`.
- NL-BIOMERO: `docs/venv/Scripts/sphinx-build.exe -b html . _build_local`, from
  its docs directory.

They retain existing documentation warnings (66 core, 19 NL-BIOMERO), including
unrelated autodoc formatting and references. Compose configuration validation
passed for the main file and `docker-compose.shallower-build.yml`; the existing
Compose `version` deprecation warning remains.

## Container and shell smoke results

The final image's mounted fixture used actual ISCC-BIO hashing, retained label
pixels, removed duplicate image arrays, preserved every canonical byte, and
reused its report unchanged on rerun. Its measured fixture timings were:

- Identity evaluation: **3.5411 s**.
- Normalization: **0.1099 s**.

The actual submission-ledger shell was also executed in an offline container
with fake `sbatch`/`sacct`: initial submission, saved-job adoption, reconciliation
after loss of the job-ID write, and a rejected submission all passed. It submitted
no real jobs.

```powershell
docker build --tag biomero-shallower:0.1.0 --file biomero-shallower/Dockerfile .
docker run --rm --network none biomero-shallower:0.1.0 health
docker run --rm --network none --volume D:/workspace/.verification/shallower-smoke-adapter:/fixture --volume D:/workspace/biomero-shallower/tools:/tools:ro --entrypoint python biomero-shallower:0.1.0 /tools/mounted_smoke.py
docker run --rm --network none --volume D:/workspace/.verification/submission-smoke:/fixture --volume D:/workspace/biomero-shallower/tools:/tools:ro --volume D:/workspace/biomero:/core:ro --entrypoint python biomero-shallower:0.1.0 /tools/submission_smoke.py
```

Use new empty fixture directories when repeating these commands. The reported
timings are small-fixture correctness measurements, not full-screen performance
claims. [The benchmark protocol](docs/benchmarks.md) records the supplied baseline
and commands for a disposable full-result copy.

## Compatibility and rollout limits

- Flag absent/false keeps local importer processing; the feature is not exposed
  as a workflow parameter. ZIP stays in use with an archive extension point.
- Only `keep-full` is supported. Pre-mutation failures safely retain full output;
  unresolved submission/recovery stops retrieval and preserves its journal.
- The importer validates small reports and canonical references, without
  regenerating pixel identities. Ordinary registration and user renaming remain
  supported.
- Matching schema/helper packages and a cluster-accessible image must be
  published or installed from local artifacts before rollout. The wheel pair
  is available here without publishing a repository. The new requirements
  intentionally reject an older schema package lacking the receipt contracts.
- Actual Apptainer/Singularity execution, live Slurm restart behavior, complete
  OMERO registration, and the 846-image performance target have not been tested
  against the running deployment. No end-to-end full-screen acceptance claim
  is made.
- Trust assumes controlled image selection, Slurm account, event store and
  import-order writers. Reports are integrity bindings, not signed attestations
  against malicious same-account workflows; see [the trust boundary](docs/architecture.md).
