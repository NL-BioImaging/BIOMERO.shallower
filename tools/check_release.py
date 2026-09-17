"""Validate declared tool/container versions before building or publishing."""

import ast
import os
from pathlib import Path
import re
import tomllib

from packaging.version import Version


def check_versions(root, release_tag=None):
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    module = ast.parse((root / "biomero_shallower/__init__.py").read_text())
    tool_versions = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets)
    ]
    labels = re.findall(r'org\.opencontainers\.image\.version="([^"]+)"',
                        (root / "Dockerfile").read_text())
    if tool_versions != [version] or labels != [version]:
        raise ValueError("Package, tool, and Dockerfile versions must agree")
    if release_tag and (not release_tag.startswith("v")
                        or Version(release_tag[1:]) != Version(version)):
        raise ValueError("Release tag must match the package version with a v prefix")
    return version


if __name__ == "__main__":
    version = check_versions(Path(__file__).resolve().parents[1],
                             os.getenv("RELEASE_TAG"))
    print(f"version={version}")
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"version={version}\n")
