from importlib.metadata import version
import subprocess
import sys

from biomero_shallower import __version__


def test_runtime_version_matches_installed_distribution():
    assert __version__ == version("biomero-shallower")


def test_cli_version_matches_installed_distribution():
    result = subprocess.run(
        [sys.executable, "-m", "biomero_shallower.cli", "--version"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == version("biomero-shallower")
