import importlib.util
from pathlib import Path

import pytest


_spec = importlib.util.spec_from_file_location(
    "check_release", Path(__file__).parents[1] / "tools/check_release.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def fixture_project(root, *, package="1.2.3", tool="1.2.3", image="1.2.3"):
    (root / "biomero_shallower").mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{package}"\n')
    (root / "biomero_shallower/__init__.py").write_text(
        f'__version__ = "{tool}"\n')
    (root / "Dockerfile").write_text(
        f'LABEL org.opencontainers.image.version="{image}"\n')


def test_matching_versions_and_release_tag(tmp_path):
    fixture_project(tmp_path)
    assert _module.check_versions(tmp_path, "v1.2.3") == "1.2.3"


def test_prerelease_tag_matches_pep440_package_version(tmp_path):
    fixture_project(tmp_path, package="1.2.3b1", tool="1.2.3b1", image="1.2.3b1")
    assert _module.check_versions(tmp_path, "v1.2.3-beta.1") == "1.2.3b1"


@pytest.mark.parametrize("kwargs", [{"tool": "1.2.4"}, {"image": "1.2.4"}])
def test_inconsistent_tool_or_image_versions_are_rejected(tmp_path, kwargs):
    fixture_project(tmp_path, **kwargs)
    with pytest.raises(ValueError, match="versions must agree"):
        _module.check_versions(tmp_path)


def test_inconsistent_release_tag_is_rejected(tmp_path):
    fixture_project(tmp_path)
    with pytest.raises(ValueError, match="Release tag"):
        _module.check_versions(tmp_path, "v1.2.4")
