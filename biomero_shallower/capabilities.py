# -*- coding: utf-8 -*-
# Copyright 2026 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Stable runtime capabilities shared by Python and container consumers.
"""

from importlib.metadata import PackageNotFoundError, metadata
import os
import re
from typing import Any

from . import __version__
from .adapters import ADAPTERS


CAPABILITY_SCHEMA = 1
RUNTIME_CONTRACTS = tuple(sorted(ADAPTERS))
SHALLOW_MANIFEST_SCHEMAS = (2,)
# Same repository as the `images:` entry of the publish job in .github/workflows/ci.yml.
# The repository is declared once, as the "Helper image" project URL in pyproject.toml,
# so the release workflow and this code read the same value. Sites that publish the
# image elsewhere set the environment variable or pass ``repository``.
IMAGE_REPOSITORY_ENV = "BIOMERO_SHALLOWER_IMAGE_REPOSITORY"
HELPER_IMAGE_URL_LABEL = "Helper image"
HELPER_IMAGE_URL_PREFIX = "https://hub.docker.com/r/"
_RELEASE_VERSION = re.compile(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?")
_PRERELEASE_LABELS = {"a": "alpha", "b": "beta", "rc": "rc"}
MIGRATIONS = (
    "canonical-single-store",
    "schema-1-to-2",
    "schema-1-path-only-labels",
)


def capabilities() -> dict[str, Any]:
    """
    Return the machine-readable compatibility contract for this build.

    Returns:
        dict[str, Any]:
            The capability schema, package version, runtime contracts,
            shallow manifest schemas and supported migrations.
    """
    return {
        "capabilitySchema": CAPABILITY_SCHEMA,
        "version": __version__,
        "contracts": list(RUNTIME_CONTRACTS),
        "manifestSchemas": list(SHALLOW_MANIFEST_SCHEMAS),
        "migrations": list(MIGRATIONS),
    }


def helper_image_repository() -> str:
    """
    Return the helper image repository this package is published to.

    The value comes from the ``BIOMERO_SHALLOWER_IMAGE_REPOSITORY`` environment
    variable if set, otherwise from the "Helper image" project URL in the
    package metadata (declared in pyproject.toml).

    Returns:
        str:
            The repository, for example `namespace/biomero-shallower`.

    Raises:
        RuntimeError: If neither the environment variable nor the package
            metadata provides it (for example in an uninstalled checkout).
    """
    configured = os.getenv(IMAGE_REPOSITORY_ENV)
    if configured:
        return configured
    try:
        project_urls = metadata("biomero-shallower").get_all("Project-URL") or []
    except PackageNotFoundError:
        project_urls = []
    for entry in project_urls:
        label, _, url = entry.partition(", ")
        if label == HELPER_IMAGE_URL_LABEL:
            return url.removeprefix(HELPER_IMAGE_URL_PREFIX)
    raise RuntimeError(
        f"No helper image repository: set {IMAGE_REPOSITORY_ENV} or install the package"
    )


def helper_image(version: str | None = None, repository: str | None = None) -> str:
    """
    Return the published helper image (repository:tag) for a package version.

    Maps a PEP 440 release version to the tag the release workflow publishes:
    `0.1.0` -> `0.1.0`, `0.1.0b9` -> `0.1.0-beta.9`, `0.1.0rc1` -> `0.1.0-rc.1`.
    Development and local versions have no published image and raise ValueError.

    Args:
        version (str | None, optional): The package version to map. Defaults
            to the installed package version.
        repository (str | None, optional): The image repository. Defaults to
            `helper_image_repository()`.

    Returns:
        str:
            The image reference as `repository:tag`.

    Raises:
        ValueError: If the version is a development or local version with no
            published image.
        RuntimeError: If no repository is given and none is configured.
    """
    version = __version__ if version is None else version
    match = _RELEASE_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"No published helper image for version {version!r}")
    release, kind, number = match.groups()
    tag = release if kind is None else f"{release}-{_PRERELEASE_LABELS[kind]}.{number}"
    return f"{repository or helper_image_repository()}:{tag}"


def require_migrations(*required: str) -> None:
    """
    Fail closed when this package cannot perform a requested migration.

    Args:
        *required (str): The migration capabilities the caller needs.

    Raises:
        RuntimeError: If any of the required migrations is not supported.
    """
    missing = sorted(set(required).difference(MIGRATIONS))
    if missing:
        raise RuntimeError(
            "Installed biomero-shallower does not support migration "
            "capabilities: " + ", ".join(missing)
        )


__all__ = [
    "CAPABILITY_SCHEMA",
    "IMAGE_REPOSITORY_ENV",
    "MIGRATIONS",
    "RUNTIME_CONTRACTS",
    "SHALLOW_MANIFEST_SCHEMAS",
    "capabilities",
    "helper_image",
    "helper_image_repository",
    "require_migrations",
]
