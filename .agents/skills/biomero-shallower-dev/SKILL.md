---
name: biomero-shallower-dev
description: BIOMERO.shallower development runbook. Use when changing biomero_shallower (CLI, shallowing transaction, NGFF adapters, pixel identity, migration), the container image, the contract and receipt versions it shares with biomero-schema and BIOMERO.importer, its tests or MkDocs documentation, or when drafting its release notes.
---

# BIOMERO.shallower Development

Required reading first: the
[shared NL-BIOMERO engineering rules](https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/nl-biomero-engineering/SKILL.md).
They cover principles, logging, branches and pull requests, and agent
guidance; they are not repeated here.

## Why shallowing exists

Reusable workflows (Bilayers, BIAFLOWS, Fractal tasks) get one input folder and
one output folder, and must write an output Zarr that is valid on its own, so
it usually repeats its input pixels (for example the image plus a new
`labels/` group). BIOMERO orchestrates the whole route: it supplies the input
folder, collects the output and stores it, so it can avoid storing those
duplicates again, which becomes costly for large plates and repeated runs.

- Shallowing applies only to BIOMERO's own copies in managed storage (the
  importer's and analyzer's `.processed`/`.analyzed` data), never to original
  acquisition data. Workflows must not be expected to clean up their output.
- A shallow result stays usable: OMERO and the viewer reconstruct it through a
  link to the canonical pixels, and a restore must be able to rebuild the full
  Zarr for a new workflow or for export to an archive outside BIOMERO.
- Comparing full-resolution pixels in the result path more than doubled
  analysis time, which users would wait for. Comparing the smallest shared
  pyramid level is therefore a deliberate performance trade-off, accepted
  because only BIOMERO-produced copies are pruned and workflows should not
  change their input pixels. Do not replace it with full-resolution comparison
  in the result path without a decision from the maintainers; exact checks
  belong in background work (see the sampling/audit and identity-cache issues).
- A workflow may declare that its outputs are source-linked (its RO-Crate run
  output, or later its descriptor or the per-workflow admin setting); BIOMERO
  treats that declaration as permission to shallow.
- The design is interim: OME-NGFF RFC-8 (collections) is expected to change
  how linked Zarr data is represented.

## Purpose and boundaries

BIOMERO.shallower is a filesystem-only library and CPU container that replaces
returned OME-Zarr arrays with references to canonical inputs. It needs no OMERO
connection (`docs/architecture.md`, "Python API").

- Owns: NGFF node discovery, ISCC-BIO pixel identity, the transactional
  shallowing with its journal and rollback, and the CLI.
- Does not own the portable report and receipt models: `biomero-schema` does.
  Add or change a contract there first, then a matching adapter here.
- BIOMERO.importer uses the package locally and validates receipts; its
  OMERO reader adapter stays in the importer.
- biomero core runs the versioned image on Slurm; biomero-scripts decides
  whether the stage runs. Neither logic belongs here.
- Never mutate or mount canonical storage, and never remove a prune journal
  to free space: it may hold the only copy of an array
  (`docs/architecture.md`, "Transaction").

Read `docs/architecture.md` before touching `operations.py`, `transaction.py`
or an adapter: the commit and rollback order is a safety property.

## Public surface

The `biomero-shallower` entry point is `biomero_shallower.cli:main`.

- Commands: `health`, `inspect`, `migrate-v1`, `verify`, `normalize`,
  `normalize-tree` (argument parser in `cli.py`).
- Each command prints one JSON document; errors print JSON to stderr.
- Exit 0 is a safe terminal result (normalized, kept full, skipped); exit 2 is
  an error (`cli.py`, `main`). Callers must read the report's `result`.
- `health` and `capabilities()` expose version, supported contracts, manifest
  schemas and migrations (`capabilities.py`). Extend those lists when adding an
  adapter or migration; callers fail closed on a missing capability.
- The Python API shared with the importer is `operations.normalize` and
  `operations.validate_report`; keep it free of OMERO dependencies.

## Configuration and version coupling

- Contract versions come from `adapters.ADAPTERS`; the CLI
  `--contract-version` choices derive from it. A new contract needs a schema
  model, an adapter with parity, rollback and import-validation tests, and a
  capability entry (`docs/architecture.md`, "Adding a contract adapter").
- `biomero-schema` is a bounded dependency in `pyproject.toml`; the container
  pins it in `requirements.lock`. Publish required schema versions before
  building a release (`docs/releases.md`).
- Consumers trust a receipt only for the exact tool version and helper image:
  `operations.validate_report` rejects a differing `tool_version` or `image`
  (`operations.py`). Do not relax this check.
- The helper image comes from `--image` or `BIOMERO_SHALLOWER_IMAGE`
  (`cli.py`); the deployment owns the flags that enable or select remote
  shallowing, not this package.
- The version is derived from Git tags by `setuptools_scm`; do not edit a
  version by hand (`docs/releases.md`).
- Schema-1 stores change only through the explicit `migrate-v1` command, which
  invalidates the report checksum: migrate settled data only.

## Testing

Use Python 3.11 or newer (`pyproject.toml`):

```sh
python -m pip install -e '.[test]'
python -m pytest tests -q
```

CI installs `.[test,identity]` and runs the same pytest command
(`.github/workflows/ci.yml`). Unit tests inject identity providers and need no
reader stack; install the `identity` extra only for real pixel hashing.

The container smoke test builds the image and runs `tests/container_smoke.py`
without network, for Zarr v2 and with `--zarr-format 3`. Use the exact build
and run commands in `docs/development.md`; the fixture directory must be
writable by UID 10001 and must never hold production data. Rebuild and smoke
test after changing `requirements.lock` or the Dockerfile. Benchmark only on a
disposable copy of a returned result.

Match the existing `tests/test_*.py` style; cover rollback and idempotent rerun
for any change to the transaction.

## Documentation

MkDocs Material with mkdocstrings; pages are listed in `mkdocs.yml`:

```sh
python -m pip install -e . -r docs/requirements.txt
python -m mkdocs build --strict
```

Pull requests build in strict mode; pushes to `main` publish GitHub Pages
(`.github/workflows/docs.yml`). Keep design in `docs/architecture.md` and
publishing steps in `docs/releases.md`; `README.md` and `docs/index.md` are the
usage overview. Generated `site/` output is not committed.

## Release notes

Read [`references/release-notes.md`](references/release-notes.md) before
drafting a stable or beta release body. The GitHub release tag owns the version
(`docs/releases.md`).
