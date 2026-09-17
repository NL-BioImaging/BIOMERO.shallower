import os
from pathlib import Path

import pytest

from biomero_shallower.pixel_identity import (
    IsccBioIdentityProvider,
    PixelIdentityError,
)


def biocode_response():
    return {
        "iscc_code": "ISCC:KTOPLEVEL",
        "generator": "iscc-bio - v0.2.0",
        "parts": [
            {
                "iscc_code": "ISCC:KSUM",
                "units": ["ISCC:GDATA", "ISCC:IINSTANCE"],
            }
        ],
    }


def test_resolves_relative_nested_zarr_node_before_alias(tmp_path, monkeypatch):
    working_directory = tmp_path / "workflow"
    node = working_directory / "plate.ome.zarr" / "A" / "1" / "0"
    node.mkdir(parents=True)
    monkeypatch.chdir(working_directory)
    aliases = []

    def make_alias(source, alias, *, target_is_directory):
        aliases.append((source, alias, target_is_directory))

    monkeypatch.setattr(os, "symlink", make_alias)
    provider = IsccBioIdentityProvider(
        generate_biocode=lambda *_args, **_kwargs: biocode_response(),
        tool_version="0.2.0",
    )

    provider.generate(
        Path("plate.ome.zarr"),
        node_path="A/1/0",
        role="image",
        shape=(1, 1, 1, 8, 8),
        dtype="uint8",
        axes=("t", "c", "z", "y", "x"),
    )

    assert aliases[0][0] == node.resolve()
    assert aliases[0][0].is_absolute()
    assert aliases[0][2] is True


def test_relative_nested_alias_resolves_on_filesystem(tmp_path, monkeypatch):
    working_directory = tmp_path / "workflow"
    node = working_directory / "plate.ome.zarr" / "A" / "1" / "0"
    node.mkdir(parents=True)
    monkeypatch.chdir(working_directory)
    resolved_sources = []

    def generate(source, *, source_type):
        source = Path(source)
        assert source_type == "bioio"
        assert source.is_symlink()
        assert source.exists()
        resolved_sources.append(source.resolve())
        return biocode_response()

    provider = IsccBioIdentityProvider(
        generate_biocode=generate,
        tool_version="0.2.0",
    )

    try:
        provider.generate(
            Path("plate.ome.zarr"),
            node_path="A/1/0",
            role="image",
            shape=(1, 1, 1, 8, 8),
            dtype="uint8",
            axes=("t", "c", "z", "y", "x"),
        )
    except PixelIdentityError as exc:
        if os.name == "nt" and isinstance(exc.__cause__, OSError):
            pytest.skip("Creating directory symlinks requires Windows privileges")
        raise

    assert resolved_sources == [node.resolve()]
