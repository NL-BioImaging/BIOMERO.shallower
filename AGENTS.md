# BIOMERO.shallower

Filesystem-only shallow-Zarr library and CPU container for BIOMERO. It compares
returned OME-Zarr images and labels with canonical inputs, replaces reusable
arrays with source references, and retains derived data. Published as the
`biomero-shallower` Python package and a Docker image; used locally by
BIOMERO.importer and on Slurm by biomero core.

## Read first

1. Shared rules for all component repositories:
   <https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/nl-biomero-engineering/SKILL.md>
2. Independent PR review procedure:
   <https://github.com/NL-BioImaging/NL-BIOMERO/blob/master/.agents/skills/nl-biomero-review/SKILL.md>
3. Component development skill (ownership, version coupling, testing, docs,
   release notes):
   [`.agents/skills/biomero-shallower-dev/SKILL.md`](.agents/skills/biomero-shallower-dev/SKILL.md)
   and the `references/` it routes to.

## Layout

- `biomero_shallower/`: package. `cli.py` and `operations.py` are the entry
  points; `discovery.py`, `evaluation.py`, `normalization.py`,
  `materialization.py` and `registration.py` hold the returned-store logic
  (`result_zarr.py` re-exports their public API); `transaction.py` is the prune
  journal; `filesystem.py`, `ngff.py` and `pixel_identity.py` are low-level;
  `adapters.py`, `capabilities.py` and `migration.py` complete the surface.
- `tests/`: pytest suite and `container_smoke.py`.
- `docs/`, `mkdocs.yml`: MkDocs documentation.
- `Dockerfile`, `requirements.lock`: container image and its pinned runtime.
- `.github/workflows/`: CI (tests, image smoke test, publish) and docs.

## Build and check

- Tests: `python -m pip install -e '.[test]'` then `python -m pytest tests -q`.
- Docs: `python -m pip install -e . -r docs/requirements.txt` then
  `python -m mkdocs build --strict`.
- Container smoke test: commands in `docs/development.md`.
- Versions come from Git tags; do not edit a version by hand.

## Related repositories

biomero (core), biomero-scripts, BIOMERO.importer, biomero-schema, OMERO.biomero,
NL-BIOMERO, BIOMERO.ZarrViewer, all under <https://github.com/NL-BioImaging>.
Each has its own `AGENTS.md`.
