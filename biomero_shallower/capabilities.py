"""Stable runtime capabilities shared by Python and container consumers."""

import re

from . import __version__
from .adapters import ADAPTERS


CAPABILITY_SCHEMA = 1
RUNTIME_CONTRACTS = tuple(sorted(ADAPTERS))
SHALLOW_MANIFEST_SCHEMAS = (2,)
IMAGE_REPOSITORY = "cellularimagingcf/biomero-shallower"
_RELEASE_VERSION = re.compile(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?")
_PRERELEASE_LABELS = {"a": "alpha", "b": "beta", "rc": "rc"}
MIGRATIONS = (
    "canonical-single-store",
    "schema-1-to-2",
    "schema-1-path-only-labels",
)


def capabilities() -> dict:
    """Return the machine-readable compatibility contract for this build."""
    return {
        "capabilitySchema": CAPABILITY_SCHEMA,
        "version": __version__,
        "contracts": list(RUNTIME_CONTRACTS),
        "manifestSchemas": list(SHALLOW_MANIFEST_SCHEMAS),
        "migrations": list(MIGRATIONS),
    }


def helper_image(version: str = __version__) -> str:
    """Return the published helper image (repository:tag) for a package version.

    Maps a PEP 440 release version to the tag the release workflow publishes:
    `0.1.0` -> `0.1.0`, `0.1.0b9` -> `0.1.0-beta.9`, `0.1.0rc1` -> `0.1.0-rc.1`.
    Development and local versions have no published image and raise ValueError.
    """
    match = _RELEASE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"No published helper image for version {version!r}")
    release, kind, number = match.groups()
    tag = release if kind is None else f"{release}-{_PRERELEASE_LABELS[kind]}.{number}"
    return f"{IMAGE_REPOSITORY}:{tag}"


def require_migrations(*required: str) -> None:
    """Fail closed when this package cannot perform a requested migration."""
    missing = sorted(set(required).difference(MIGRATIONS))
    if missing:
        raise RuntimeError(
            "Installed biomero-shallower does not support migration "
            "capabilities: " + ", ".join(missing)
        )


__all__ = [
    "IMAGE_REPOSITORY",
    "CAPABILITY_SCHEMA",
    "MIGRATIONS",
    "RUNTIME_CONTRACTS",
    "SHALLOW_MANIFEST_SCHEMAS",
    "capabilities",
    "helper_image",
    "require_migrations",
]
