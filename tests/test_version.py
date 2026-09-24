from importlib.metadata import version
import subprocess
import sys

from biomero_shallower import __version__
from biomero_shallower.capabilities import (
    MIGRATIONS,
    capabilities,
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
