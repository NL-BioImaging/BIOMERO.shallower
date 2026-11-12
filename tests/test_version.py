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
from importlib.metadata import PackageNotFoundError, version
import subprocess
import sys

import pytest

from biomero_shallower import __version__
from biomero_shallower.capabilities import (
    MIGRATIONS,
    capabilities,
    helper_image,
    helper_image_repository,
    require_migrations,
)


def test_runtime_version_matches_installed_distribution():
    assert __version__ == version("biomero-shallower")


def test_cli_version_matches_installed_distribution():
    result = subprocess.run(
        [sys.executable, "-m", "biomero_shallower.cli", "--version"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == version("biomero-shallower")


def test_capabilities_describe_runtime_and_migrations():
    result = capabilities()

    assert result == {
        "capabilitySchema": 1,
        "version": __version__,
        "contracts": [1],
        "manifestSchemas": [2],
        "migrations": [
            "canonical-single-store",
            "schema-1-to-2",
            "schema-1-path-only-labels",
        ],
    }
    require_migrations(*MIGRATIONS)


def test_missing_migration_capability_fails_closed():
    import pytest

    with pytest.raises(RuntimeError, match="future-migration"):
        require_migrations("future-migration")


def test_container_labels_match_python_capabilities():
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert 'org.biomeroproject.shallower.capability-schema="1"' in dockerfile
    assert 'org.biomeroproject.shallower.runtime-contracts="1"' in dockerfile
    assert 'org.biomeroproject.shallower.manifest-schemas="2"' in dockerfile
    assert (
        'org.biomeroproject.shallower.migrations="'
        + ",".join(MIGRATIONS)
        + '"'
    ) in dockerfile


@pytest.mark.parametrize("version, tag", [
    ("0.1.0", "0.1.0"),
    ("1.2.3", "1.2.3"),
    ("0.1.0b9", "0.1.0-beta.9"),
    ("0.1.0rc1", "0.1.0-rc.1"),
    ("0.1.0a2", "0.1.0-alpha.2"),
])
def test_helper_image_maps_pep440_to_published_tag(version, tag):
    assert helper_image(version, repository="site/helper") == f"site/helper:{tag}"


@pytest.mark.parametrize("version", ["0.0.dev0", "0.1.0b9.dev3+g1234", "0.1.0.post1"])
def test_helper_image_rejects_unpublished_versions(version):
    with pytest.raises(ValueError, match="No published helper image"):
        helper_image(version)


def test_helper_image_defaults_to_the_installed_release_version(monkeypatch):
    monkeypatch.setattr("biomero_shallower.capabilities.__version__", "0.1.0b9")
    monkeypatch.setenv("BIOMERO_SHALLOWER_IMAGE_REPOSITORY", "site/helper")
    assert helper_image() == "site/helper:0.1.0-beta.9"


def test_helper_image_repository_comes_from_the_package_metadata(monkeypatch):
    monkeypatch.delenv("BIOMERO_SHALLOWER_IMAGE_REPOSITORY", raising=False)
    try:
        version("biomero-shallower")
    except PackageNotFoundError:
        pytest.skip("the package is not installed")
    assert helper_image_repository() == "cellularimagingcf/biomero-shallower"


def test_helper_image_repository_environment_override(monkeypatch):
    monkeypatch.setenv("BIOMERO_SHALLOWER_IMAGE_REPOSITORY", "site/helper")
    assert helper_image_repository() == "site/helper"


def test_helper_image_defaults_to_the_installed_version_and_rejects_a_dev_build(monkeypatch):
    monkeypatch.setattr("biomero_shallower.capabilities.__version__", "0.1.0b9.dev3+g1234")
    with pytest.raises(ValueError, match="No published helper image"):
        helper_image()
