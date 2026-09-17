# Development

## Unit tests

Create a repository-local virtual environment using Python 3.11 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest tests -q
```

On Windows, activate with `.venv\Scripts\Activate.ps1` instead.
Install the `identity` extra for real pixel hashing. Unit tests use injected
identity providers so they do not require the full reader stack.

## Container smoke test

The Dockerfile builds from this repository. Runtime dependencies are pinned
in `requirements.lock`; the build-context allowlist excludes credentials and
local data. The smoke test checks real ISCC-BIO hashing, label retention,
canonical immutability, and idempotent normalization without network access.

From the repository root in a POSIX shell:

```sh
docker build -t biomero-shallower:local .
docker run --rm --network none biomero-shallower:local health
smoke_dir=$(mktemp -d)
chmod 777 "$smoke_dir"
docker run --rm --network none \
  -v "$smoke_dir:/fixture" \
  -v "$PWD/tests:/tests:ro" \
  --entrypoint python biomero-shallower:local /tests/container_smoke.py
```

The disposable fixture directory must be writable by UID 10001. Do not use
production data as the fixture mount. GitHub Actions runs the same smoke test.

## Dependency updates

Update `requirements.lock` in a clean Python 3.12 Linux environment with the
identity extra installed. Include the published schema package and all
transitive identity dependencies. Do not copy an importer's entire environment
or add local editable installs. Rebuild the image and run its smoke test after
updating pins.

## Performance measurements

Use a disposable returned-result copy; normalization intentionally removes
verified duplicate arrays. Never benchmark against canonical storage.

```sh
/usr/bin/time -v biomero-shallower normalize \
  --returned-zarr /benchmark/result.zarr \
  --canonical-inputs /benchmark/input.json --contract-version 1 \
  --identity-workers 4 --failure-policy keep-full \
  --report /benchmark/report.json --benchmark-bytes
du -sb /benchmark/result.zarr
```

Record identity and normalization timings separately, with input size, worker
count, filesystem, and cache state. `--benchmark-bytes` enables expensive
recursive byte measurements; production runs omit them. The container smoke
test checks correctness and cannot predict full-screen throughput.

## Documentation

```sh
python -m pip install -e . -r docs/requirements.txt
python -m mkdocs serve
python -m mkdocs build --strict
```

Generated `site/` output is ignored. Pull requests build documentation in
strict mode; pushes to `main` publish GitHub Pages.
