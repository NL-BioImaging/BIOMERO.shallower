"""Filesystem metadata for NGFF 0.4/v2 and 0.5/v3, without reading pixels."""
import json
from pathlib import Path


class PixelIdentityError(RuntimeError):
    """An exact pixel identity or its filesystem metadata is ambiguous."""


def read_json_object(path, description):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PixelIdentityError(f"Cannot read {description} metadata: {path}") from exc
    if not isinstance(value, dict):
        raise PixelIdentityError(f"Invalid {description} metadata: {path}")
    return value


def metadata_path(node):
    node = Path(node)
    v3 = node / "zarr.json"
    if v3.exists():
        if any((node / name).exists() for name in (".zattrs", ".zgroup", ".zarray")):
            raise PixelIdentityError(f"Ambiguous v2/v3 metadata: {node}")
        return v3
    return node / ".zattrs"


def group_metadata(node):
    path = metadata_path(node)
    doc = read_json_object(path, "NGFF group")
    if path.name == ".zattrs":
        return path, doc, doc, 2
    if doc.get("zarr_format") != 3 or doc.get("node_type") != "group":
        raise PixelIdentityError(f"Expected a Zarr v3 group: {path}")
    # A cached tree could still advertise omitted arrays after normalization.
    # Keep these stores full until consolidated-metadata rewriting is supported.
    if doc.get("consolidated_metadata") is not None:
        raise PixelIdentityError(f"Consolidated Zarr v3 metadata is unsupported: {path}")
    attrs = doc.get("attributes", {})
    ome = attrs.get("ome") if isinstance(attrs, dict) else None
    if not isinstance(ome, dict) or ome.get("version") != "0.5":
        raise PixelIdentityError(f"Only OME-Zarr 0.5/v3 metadata is supported: {path}")
    return path, doc, ome, 3


def read_attrs(node):
    return group_metadata(node)[2]


def interchange_profile(node):
    return {2: "ngff-0.4-zarr-v2", 3: "ngff-0.5-zarr-v3"}[group_metadata(node)[3]]


def encoded_attrs(node, attrs):
    """Replace NGFF attributes while retaining the v3 envelope and extensions."""
    path, doc, _, version = group_metadata(node)
    if version == 2:
        return path, attrs
    doc["attributes"]["ome"] = attrs
    return path, doc


def new_group_metadata(node, attrs, version):
    node = Path(node)
    if version == 2:
        return {node / ".zgroup": {"zarr_format": 2}, node / ".zattrs": attrs}
    if version == 3:
        return {node / "zarr.json": {
            "zarr_format": 3, "node_type": "group",
            "attributes": {"ome": {"version": "0.5", **attrs}},
        }}
    raise PixelIdentityError(f"Unsupported Zarr format: {version}")
