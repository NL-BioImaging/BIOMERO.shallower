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
the real-identity normalization smoke test in `tests/container_smoke.py`
without network access.
Pull-request builds do not push an image. Publishing requires successful tests
for the release commit; the container is pushed only after its smoke test passes.

1. Review `requirements.lock`, including its exact published schema version,
   and run the tests. Publish required schema contracts before building.
2. Merge the reviewed changes and publish a GitHub release with a version tag,
   such as `v0.1.0-beta.1` for a prerelease or `v0.1.0` for a stable release.
   Mark beta releases as prereleases in GitHub.
3. Check the container and PyPI workflows. Confirm the versioned Docker Hub
   tag and Python distribution are both available before updating NL-BIOMERO.
4. Set the matching helper image and tool version in the deployment, then use
   Slurm Init to acquire the image and Check Setup to verify availability.

The GitHub release tag owns the version; no source-file version bump is needed.
Like BIOMERO core, this package uses `setuptools_scm` to derive versions from
Git tags. Release builds explicitly use the published tag. The installed
package metadata supplies the CLI and report tool version, and CI passes that
same version into Docker rather than copying Git history into the image.

For `v0.1.0-beta.1`, PyPI and the helper report version `0.1.0b1`, while Docker
Hub receives `cellularimagingcf/biomero-shallower:0.1.0-beta.1`. The OCI version
label matches the normalized package version. Stable releases publish the full
version, a moving major/minor alias, and `latest`; for example, `v0.1.0`
publishes `0.1.0`, `0.1`, and `latest` after smoke testing. Prereleases publish
only their full version and do not update the stable aliases. Pin the full
prerelease image tag when deploying a beta. CI checks the built container's
reported version and OCI label against the package version.

Untagged checkouts have SCM-derived development versions. Bare Docker builds
without a supplied SCM version use the explicit non-release fallback
`0.0.dev0`; see [Development](development.md) for a version-aware local build.

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
