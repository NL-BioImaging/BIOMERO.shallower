"""ISCC-BIO adapter for BIOMERO pixel identity contracts.

Hashing and IMAGEWALK traversal stay in the upstream ``iscc-bio`` package. This
module only selects one explicit image node, validates the public API result,
and combines it with the semantic guard required by BIOMERO's shared contract.
"""

from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
import os
from itertools import product
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, Mapping, Sequence

from biomero_schema.zarr import PixelIdentity, PIXEL_COMPARISON_PROFILE
import numpy as np
from .ngff import PixelIdentityError, group_metadata, read_json_object


BiocodeCallable = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class ZarrNodeSemanticGuard:
    """Relevant metadata for the highest-resolution array of one NGFF node."""

    shape: tuple[int, ...]
    dtype: str
    axes: tuple[str, ...]
    coordinate_transformations: tuple[dict[str, Any], ...]
    ngff_version: str
    zarr_format: int
    dataset_path: str = "."


def _validate_node_path(node_path: str) -> PurePosixPath:
    if not node_path or "\\" in node_path:
        raise PixelIdentityError(f"Unsafe Zarr node path: {node_path!r}")
    parsed = PurePosixPath(node_path)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise PixelIdentityError(f"Unsafe Zarr node path: {node_path!r}")
    return parsed


def _read_json_object(path: Path, description: str) -> dict[str, Any]:
    return read_json_object(path, description)


def read_zarr_semantic_guard(
    zarr_root: str | Path, node_path: str, *, dataset_path: str | None = None
) -> ZarrNodeSemanticGuard:
    """Read one explicit NGFF 0.4/v2 or 0.5/v3 image's semantic guard."""
    parsed_node = _validate_node_path(node_path)
    node = Path(zarr_root)
    if node_path != ".":
        node = node.joinpath(*parsed_node.parts)

    _, _, attributes, zarr_format = group_metadata(node)
    multiscales = attributes.get("multiscales")
    if not isinstance(multiscales, list) or len(multiscales) != 1:
        raise PixelIdentityError(
            "NGFF image metadata must contain exactly one multiscales entry"
        )
    multiscale = multiscales[0]
    ngff_version = "0.4" if zarr_format == 2 else "0.5"
    if not isinstance(multiscale, dict) or (
        zarr_format == 2 and multiscale.get("version") != ngff_version
    ):
        raise PixelIdentityError("Unsupported NGFF image metadata")

    raw_axes = multiscale.get("axes")
    if not isinstance(raw_axes, list) or not raw_axes:
        raise PixelIdentityError("NGFF image metadata has no named axes")
    axes = []
    for axis in raw_axes:
        name = axis.get("name") if isinstance(axis, dict) else axis
        if not isinstance(name, str) or not name:
            raise PixelIdentityError("NGFF image metadata has an invalid axis")
        axes.append(name)

    datasets = multiscale.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise PixelIdentityError("NGFF image metadata has no datasets")
    highest_resolution = (datasets[0] if dataset_path is None else next(
        (item for item in datasets if item.get("path") == dataset_path), None))
    if not isinstance(highest_resolution, dict):
        raise PixelIdentityError("NGFF dataset metadata is invalid")
    dataset_path = highest_resolution.get("path")
    if not isinstance(dataset_path, str):
        raise PixelIdentityError("NGFF dataset path is missing")
    parsed_dataset = _validate_node_path(dataset_path)
    array_path = node
    if dataset_path != ".":
        array_path = node.joinpath(*parsed_dataset.parts)

    array = _read_json_object(
        array_path / (".zarray" if zarr_format == 2 else "zarr.json"), "Zarr array"
    )
    if array.get("zarr_format") != zarr_format or (
        zarr_format == 3 and array.get("node_type") != "array"
    ):
        raise PixelIdentityError("Image group and array Zarr formats differ")
    shape = array.get("shape")
    if (
        not isinstance(shape, list)
        or not shape
        or any(not isinstance(size, int) or size < 1 for size in shape)
    ):
        raise PixelIdentityError("Zarr array shape is invalid")
    if len(shape) != len(axes):
        raise PixelIdentityError("Zarr array shape does not match NGFF axes")
    try:
        dtype = np.dtype(array["dtype" if zarr_format == 2 else "data_type"]).name
    except (KeyError, TypeError, ValueError) as exc:
        raise PixelIdentityError("Zarr array dtype is invalid") from exc

    transformations = highest_resolution.get("coordinateTransformations", [])
    group_transformations = multiscale.get("coordinateTransformations", [])
    if not isinstance(group_transformations, list):
        raise PixelIdentityError("NGFF group coordinate transformations are invalid")
    if isinstance(transformations, list):
        transformations = transformations + group_transformations
    if not isinstance(transformations, list) or any(
        not isinstance(item, dict) for item in transformations
    ):
        raise PixelIdentityError("NGFF coordinate transformations are invalid")

    return ZarrNodeSemanticGuard(
        shape=tuple(shape),
        dtype=dtype,
        axes=tuple(axes),
        coordinate_transformations=tuple(dict(item) for item in transformations),
        ngff_version=ngff_version,
        zarr_format=zarr_format,
        dataset_path=dataset_path,
    )


def read_zarr_comparison_levels(zarr_root, node_path):
    """Metadata-only levels preserving dtype and every non-spatial position."""
    root = Path(zarr_root)
    node = root if node_path == "." else root / _validate_node_path(node_path)
    attrs = group_metadata(node)[2]
    full = read_zarr_semantic_guard(root, node_path)
    if len(set(full.axes)) != len(full.axes) or not {"x", "y"} <= set(full.axes):
        raise PixelIdentityError("Image axes must contain unique X/Y axes")
    if set(full.axes) - {"t", "c", "z", "y", "x"}:
        raise PixelIdentityError("Unsupported image axes")
    levels = []
    for dataset in attrs["multiscales"][0]["datasets"]:
        if not isinstance(dataset, dict) or not isinstance(dataset.get("path"), str):
            raise PixelIdentityError("Invalid comparison dataset metadata")
        level = read_zarr_semantic_guard(root, node_path, dataset_path=dataset["path"])
        if (level.dtype == full.dtype and all(
                level.shape[i] == size if axis not in {"x", "y"}
                else level.shape[i] <= size
                for i, (axis, size) in enumerate(zip(full.axes, full.shape)))):
            levels.append(level)
    return tuple(sorted(levels, key=lambda level: np.prod(level.shape)))


def comparison_level_matches(level, identity):
    """Dataset spelling is provenance, not a compatibility condition."""
    return (identity is not None and identity.is_current_profile
            and level.shape == identity.comparison_shape
            and level.dtype == identity.dtype and level.axes == identity.axes
            and level.coordinate_transformations == identity.comparison_coordinate_transformations)


def _zarr_planes(array, axes):
    """Decode planes in IMAGEWALK Z/C/T order; upstream owns byte encoding."""
    Plane = import_module("iscc_bio.imagewalk.common").Plane
    sizes = dict(zip(axes, array.shape))
    for z, c, t in product(range(sizes.get("z", 1)), range(sizes.get("c", 1)),
                           range(sizes.get("t", 1))):
        position = {"z": z, "c": c, "t": t}
        pixels = np.asarray(array[tuple(slice(None) if axis in {"x", "y"}
                                       else position[axis] for axis in axes)])
        if [axis for axis in axes if axis in {"x", "y"}] == ["x", "y"]:
            pixels = pixels.T
        yield Plane(pixels, 0, z, c, t)


def read_zarr_v2_semantic_guard(zarr_root, node_path):
    """Compatibility API for consumers that still require a v2-only profile."""
    guard = read_zarr_semantic_guard(zarr_root, node_path)
    if guard.zarr_format != 2:
        raise PixelIdentityError("Only Zarr v2 arrays are supported by this consumer")
    return guard


class IsccBioIdentityProvider:
    """Generate one fingerprint through upstream's pixels-only API.

    ``generate_biocode`` and ``tool_version`` are injectable so callers can test
    orchestration without importing the sizeable reader stack. Production use
    loads the public API lazily from the optional ``identity`` dependency.
    """

    def __init__(
        self,
        *,
        generate_biocode: BiocodeCallable | None = None,
        tool_version: str | None = None,
        imagewalk_revision: str | None = None,
    ) -> None:
        self._generate_biocode = generate_biocode
        self._tool_version = tool_version
        self._imagewalk_revision = imagewalk_revision

    def _load_upstream(self) -> tuple[BiocodeCallable, str]:
        generate = self._generate_biocode
        if generate is None:
            try:
                generate = import_module("iscc_bio.biocode").generate_biocode
            except ImportError as exc:
                raise PixelIdentityError(
                    "ISCC-BIO is unavailable; install biomero-shallower with "
                    "the identity extra"
                ) from exc

        tool_version = self._tool_version
        if tool_version is None:
            try:
                tool_version = version("iscc-bio")
            except PackageNotFoundError as exc:
                raise PixelIdentityError(
                    "Cannot determine the installed iscc-bio version"
                ) from exc
        return generate, tool_version

    def _import_upstream(self):
        return import_module("iscc_bio.api")

    def generate(
        self,
        zarr_root: str | Path,
        *,
        node_path: str,
        role: Literal["image", "label"],
        shape: Sequence[int],
        dtype: str,
        axes: Sequence[str],
        coordinate_transformations: Sequence[Mapping[str, Any]] = (),
        full_resolution: bool = False,
        dataset_path: str | None = None,
    ) -> PixelIdentity:
        """Hash exactly one Zarr image node and attach its semantic guard."""
        parsed_node = _validate_node_path(node_path)
        generate, tool_version = self._load_upstream()
        target = Path(zarr_root).resolve(strict=True)
        if node_path != ".":
            target = target.joinpath(*parsed_node.parts).resolve(strict=True)
        full = read_zarr_semantic_guard(zarr_root, node_path)
        if (tuple(shape), dtype, tuple(axes), tuple(coordinate_transformations)) != (
                full.shape, full.dtype, full.axes, full.coordinate_transformations):
            raise PixelIdentityError("Supplied semantics differ from image metadata")
        levels = read_zarr_comparison_levels(zarr_root, node_path)
        selected = (full if full_resolution or role == "label" else levels[0])
        if dataset_path is not None:
            selected = next((level for level in levels if level.dataset_path == dataset_path), None)
            if selected is None or (role == "label" and selected != full):
                raise PixelIdentityError("Requested comparison level is incompatible")
        try:
            array = import_module("zarr").open_array(str(target / selected.dataset_path), mode="r")
            result = {"parts": generate(_zarr_planes(array, full.axes))}
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            raise PixelIdentityError("Cannot read selected Zarr pixels") from exc
        identity = self._build_identity(
            result,
            source_description="An explicit Zarr node",
            tool_version=tool_version,
            node_path=node_path,
            role=role,
            shape=shape,
            dtype=dtype,
            axes=axes,
            coordinate_transformations=coordinate_transformations,
        )
        return identity.model_copy(update={
            "dataset_path": selected.dataset_path,
            "comparison_shape": selected.shape,
            "comparison_coordinate_transformations": selected.coordinate_transformations,
        })

    def _build_identity(
        self,
        response: Mapping[str, Any],
        *,
        source_description: str,
        tool_version: str,
        node_path: str,
        role: Literal["image", "label"],
        shape: Sequence[int],
        dtype: str,
        axes: Sequence[str],
        coordinate_transformations: Sequence[Mapping[str, Any]],
    ) -> PixelIdentity:
        """Validate one public API result and produce the shared contract."""
        parts = response.get("parts")
        if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)):
            raise PixelIdentityError("iscc-bio must return an ordered parts list")
        if len(parts) != 1:
            raise PixelIdentityError(
                f"{source_description} must produce exactly one scene; "
                f"iscc-bio returned {len(parts)}"
            )

        result = parts[0]
        if not isinstance(result, Mapping):
            raise PixelIdentityError("iscc-bio returned an invalid image part")
        units = result.get("units")
        if not isinstance(units, Sequence) or isinstance(units, (str, bytes)):
            units = ()
        if len(units) != 2:
            raise PixelIdentityError(
                "iscc-bio must return ordered Data and Instance code units"
            )

        try:
            return PixelIdentity(
                node_path=node_path,
                role=role,
                iscc_code=result["iscc_code"],
                data_code=units[0],
                instance_code=units[1],
                tool_version=tool_version,
                imagewalk_revision=(
                    self._imagewalk_revision
                    or str(
                        response.get("generator")
                        or f"iscc-bio/{tool_version}"
                    )
                ),
                shape=tuple(shape),
                dtype=dtype,
                axes=tuple(axes),
                coordinate_transformations=tuple(
                    dict(item) for item in coordinate_transformations
                ),
                comparison_profile=PIXEL_COMPARISON_PROFILE,
                dataset_path=".",
                comparison_shape=tuple(shape),
                comparison_coordinate_transformations=tuple(
                    dict(item) for item in coordinate_transformations),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PixelIdentityError(
                "iscc-bio returned an invalid pixel identity result"
            ) from exc


def pixel_identities_match(left: PixelIdentity, right: PixelIdentity) -> bool:
    """Return whether exact bytes and their NGFF semantic guards agree.

    Node paths and aggregate/data codes are intentionally excluded: a raw scene
    and its canonical Zarr node can live at different paths, while the Instance
    Code is the exact byte identity used for deduplication.
    """
    return (
        left is not None and right is not None
        and left.is_current_profile and right.is_current_profile
        and left.comparison_profile == right.comparison_profile
        and left.code_bits == right.code_bits
        and left.comparison_shape == right.comparison_shape
        and left.comparison_coordinate_transformations == right.comparison_coordinate_transformations
        and left.instance_code == right.instance_code
        and left.role == right.role
        and left.shape == right.shape
        and left.dtype == right.dtype
        and left.axes == right.axes
        and left.coordinate_transformations == right.coordinate_transformations
    )


__all__ = [
    "IsccBioIdentityProvider",
    "PixelIdentityError",
    "ZarrNodeSemanticGuard",
    "pixel_identities_match",
    "read_zarr_v2_semantic_guard",
    "read_zarr_semantic_guard",
]
