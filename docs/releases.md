# Releases and publication

BIOMERO.shallower publishes a Python package and a CPU-only Docker image.
The importer uses the package for local normalization and report validation;
BIOMERO core acquires the helper image through Apptainer or Singularity for
remote normalization. Both must identify the same tool version.

## One-time repository configuration

In GitHub, open **Settings → Secrets and variables → Actions** for
`NL-BioImaging/BIOMERO.shallower`. Add repository secrets or grant this repository
access to existing organization secrets:

| Secret | Purpose |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub login with write access to `cellularimagingcf/biomero-shallower`. |
| `DOCKERHUB_TOKEN` | Docker Hub access token for that login. Do not use a password or commit the token. |
| `PYPI_API_TOKEN` | PyPI API token permitted to publish the `biomero-shallower` project. |

Ensure `cellularimagingcf/biomero-shallower` is public so clusters can acquire
the image without registry credentials. For a different registry namespace,
adapt the workflow image reference and the corresponding deployment setting
together. Tokens are used only in publishing jobs, never in pull-request builds
or container build arguments.

For documentation, select **Settings → Pages → Source → GitHub Actions**.
The separate Documentation workflow builds MkDocs in strict mode for pull
requests and publishes the Material site after a push to `main`. The site URL
is `https://nl-bioimaging.github.io/BIOMERO.shallower/`; add it to the repository's
**About → Website** field. Generated `site/` files are not committed.

## Validation and release

The CI workflow runs unit tests and builds the wheel and source distribution.
The container workflow builds a Linux/amd64 image, runs `health`, and executes
the mounted real-identity normalization smoke test without network access.
Pull-request builds do not push an image. Publishing requires successful tests
for the release commit; the container is pushed only after its smoke test passes.

1. Update the version in `pyproject.toml`, `biomero_shallower/__init__.py`, and
   the Dockerfile version label together. CI rejects inconsistent versions.
2. Review `requirements.lock`, including its exact published schema version,
   and run the tests. Publish required schema contracts before building.
3. Merge the reviewed changes and publish a GitHub release tagged `v` followed
   by that package version. For version `0.1.0`, use `v0.1.0`.
4. Check the container and PyPI workflows. Confirm the versioned Docker Hub
   tag and Python distribution are both available before updating NL-BIOMERO.
5. Set the matching helper image and tool version in the deployment, then use
   Slurm Init to acquire the image and Check Setup to verify availability.

The release workflow checks that the tag matches the package/tool version.
Equivalent prerelease spellings are supported: package version `0.1.0b1`
can use GitHub release tag `v0.1.0-beta.1`.
The Docker image receives the exact version tag without the leading `v`.
Prereleases do not update `latest`; stable releases do so after smoke testing.
Changing only the GitHub tag does not change the helper's reported tool version.

Container runtime dependencies are pinned; release builds do not take schema
code from a moving GitHub branch or a sibling checkout. The build-context
allowlist excludes local data, credentials, and test fixtures. The helper runs
as UID/GID `10001:10001`, and writable result mounts must permit that identity.

## Licensing

This repository is licensed under **Apache-2.0**. Package metadata and the
image licence label use the same declaration, and wheels, source distributions,
and images retain the licence file. Dependency licences and notices remain
applicable to the included third-party software. The helper uses the filesystem
identity implementation, not the OMERO client libraries.
