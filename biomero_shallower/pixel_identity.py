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
ISCC-BIO adapter for BIOMERO pixel identity contracts.

Canonical pixel encoding and hashing stay in upstream ``iscc-bio``. This
adapter selects stored array planes in IMAGEWALK order and records their
resolution alongside the full-image semantic guard.
"""

from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from itertools import product
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence

from biomero_schema.zarr import PixelIdentity, PIXEL_COMPARISON_PROFILE
import numpy as np
from .ngff import PixelIdentityError, group_metadata, read_json_object


BiocodeCallable = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class ZarrNodeSemanticGuard:
    """
    Relevant metadata for the highest-resolution array of one NGFF node.

    Attributes:
        shape (tuple[int, ...]): The array shape.
        dtype (str): The NumPy dtype name.
        axes (tuple[str, ...]): The NGFF axis names, in array order.
        coordinate_transformations (tuple[dict[str, Any], ...]): The dataset and
            group coordinate transformations.
        ngff_version (str): The NGFF version ("0.4" for Zarr v2, "0.5" for v3).
        zarr_format (int): The Zarr format version (2 or 3).
        dataset_path (str): The dataset path inside the node.
    """

    shape: tuple[int, ...]
    dtype: str
    axes: tuple[str, ...]
    coordinate_transformations: tuple[dict[str, Any], ...]
    ngff_version: str
    zarr_format: int
    dataset_path: str = "."


def _validate_node_path(node_path: str) -> PurePosixPath:
    """
    Parse a Zarr node path, rejecting empty, backslash, absolute and escaping paths.

    Args:
        node_path (str): The node path inside the Zarr store.

    Returns:
        PurePosixPath:
            The parsed path.

    Raises:
        PixelIdentityError: If the path is unsafe.
    """
    if not node_path or "\\" in node_path:
        raise PixelIdentityError(f"Unsafe Zarr node path: {node_path!r}")
    parsed = PurePosixPath(node_path)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise PixelIdentityError(f"Unsafe Zarr node path: {node_path!r}")
    return parsed


def _read_json_object(path: Path, description: str) -> dict[str, Any]:
    """
    Read one JSON object file.

    Args:
        path (Path): The JSON file.
        description (str): What the file is, for error messages.

    Returns:
        dict[str, Any]:
            The parsed JSON object.
    """
    return read_json_object(path, description)


def read_zarr_semantic_guard(
    zarr_root: str | Path, node_path: str, *, dataset_path: str | None = None
) -> ZarrNodeSemanticGuard:
    """
    Read one explicit NGFF 0.4/v2 or 0.5/v3 image's semantic guard.

    Args:
        zarr_root (str | Path): The Zarr store root.
        node_path (str): The image node path inside the store ("." for the root).
        dataset_path (str, optional): The dataset to read; defaults to the first
            (highest-resolution) dataset.

    Returns:
        ZarrNodeSemanticGuard:
            The semantic guard of the selected dataset.

    Raises:
        PixelIdentityError: If the node path is unsafe or the NGFF or Zarr
            metadata is missing, inconsistent or unsupported.
    """
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


def read_zarr_comparison_levels(
    zarr_root: str | Path, node_path: str
) -> tuple[ZarrNodeSemanticGuard, ...]:
    """
    Metadata-only levels preserving dtype and every non-spatial position.

    Args:
        zarr_root (str | Path): The Zarr store root.
        node_path (str): The image node path inside the store.

    Returns:
        tuple[ZarrNodeSemanticGuard, ...]:
            The comparable levels, smallest first.

    Raises:
        PixelIdentityError: If the axes are unsupported or a dataset entry is
            invalid.
    """
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


def comparison_level_matches(level: ZarrNodeSemanticGuard, identity: PixelIdentity | None) -> bool:
    """
    Dataset spelling is provenance, not a compatibility condition.

    Args:
        level (ZarrNodeSemanticGuard): The comparison level to test.
        identity (PixelIdentity | None): The recorded pixel identity.

    Returns:
        bool:
            True if the level matches the identity's current-profile comparison
            shape, dtype, axes and coordinate transformations.
    """
    return (identity is not None and identity.is_current_profile
            and level.shape == identity.comparison_shape
            and level.dtype == identity.dtype and level.axes == identity.axes
            and level.coordinate_transformations == identity.comparison_coordinate_transformations)


def _zarr_planes(array: Any, axes: Sequence[str]) -> Iterator[Any]:
    """
    Decode planes in IMAGEWALK Z/C/T order; upstream owns byte encoding.

    Args:
        array (Any): The opened Zarr array.
        axes (Sequence[str]): The axis names, in array order.

    Yields:
        Any: One upstream ``Plane`` per Z/C/T position.
    """
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


def read_zarr_v2_semantic_guard(zarr_root: str | Path, node_path: str) -> ZarrNodeSemanticGuard:
    """
    Compatibility API for consumers that still require a v2-only profile.

    Args:
        zarr_root (str | Path): The Zarr store root.
        node_path (str): The image node path inside the store.

    Returns:
        ZarrNodeSemanticGuard:
            The semantic guard of the image.

    Raises:
        PixelIdentityError: If the array is not Zarr v2 or the metadata is invalid.
    """
    guard = read_zarr_semantic_guard(zarr_root, node_path)
    if guard.zarr_format != 2:
        raise PixelIdentityError("Only Zarr v2 arrays are supported by this consumer")
    return guard


class IsccBioIdentityProvider:
    """
    Generate one fingerprint through upstream's pixels-only API.

    ``generate_biocode`` and ``tool_version`` are injectable so callers can test
    orchestration without importing the sizeable reader stack. Production use
    loads the public API lazily from the optional ``identity`` dependency.

    Attributes:
        _generate_biocode (BiocodeCallable | None): Injected upstream generator.
        _tool_version (str | None): Injected upstream version.
        _imagewalk_revision (str | None): Injected IMAGEWALK revision.
    """

    def __init__(
        self,
        *,
        generate_biocode: BiocodeCallable | None = None,
        tool_version: str | None = None,
        imagewalk_revision: str | None = None,
    ) -> None:
        """
        Create a provider.

        Args:
            generate_biocode (BiocodeCallable, optional): Replaces upstream's
                ``generate_biocode``; loaded lazily when None.
            tool_version (str, optional): Replaces the installed ``iscc-bio``
                version; looked up lazily when None.
            imagewalk_revision (str, optional): Overrides the recorded IMAGEWALK
                revision; derived from the upstream result when None.
        """
        self._generate_biocode = generate_biocode
        self._tool_version = tool_version
        self._imagewalk_revision = imagewalk_revision

    def _load_upstream(self) -> tuple[BiocodeCallable, str]:
        """
        Resolve the upstream generator and its version.

        Returns:
            tuple[BiocodeCallable, str]:
                The generator and the ``iscc-bio`` version.

        Raises:
            PixelIdentityError: If ISCC-BIO is not installed or its version
                cannot be determined.
        """
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
        """
        Hash exactly one Zarr image node and attach its semantic guard.

        Args:
            zarr_root (str | Path): The Zarr store root.
            node_path (str): The image node path inside the store.
            role (Literal["image", "label"]): Whether the node is an image or a label.
            shape (Sequence[int]): The expected full-resolution shape.
            dtype (str): The expected dtype name.
            axes (Sequence[str]): The expected axis names.
            coordinate_transformations (Sequence[Mapping[str, Any]], optional): The
                expected coordinate transformations.
            full_resolution (bool, optional): Hash the full-resolution level
                instead of the smallest comparison level.
            dataset_path (str, optional): Hash this comparison level.

        Returns:
            PixelIdentity:
                The identity of the selected level.

        Raises:
            PixelIdentityError: If the supplied semantics differ from the image
                metadata, the requested level is incompatible, the pixels cannot
                be read, or upstream returns an invalid result.
        """
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
        """
        Validate one public API result and produce the shared contract.

        Args:
            response (Mapping[str, Any]): The upstream result.
            source_description (str): What was hashed, for error messages.
            tool_version (str): The ``iscc-bio`` version.
            node_path (str): The image node path.
            role (Literal["image", "label"]): Whether the node is an image or a label.
            shape (Sequence[int]): The array shape.
            dtype (str): The dtype name.
            axes (Sequence[str]): The axis names.
            coordinate_transformations (Sequence[Mapping[str, Any]]): The
                coordinate transformations.

        Returns:
            PixelIdentity:
                The identity built from the single returned scene.

        Raises:
            PixelIdentityError: If the result does not hold exactly one scene
                with ordered Data and Instance code units.
        """
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
    """
    Return whether exact bytes and their NGFF semantic guards agree.

    Node paths and aggregate/data codes are intentionally excluded: a raw scene
    and its canonical Zarr node can live at different paths, while the Instance
    Code is the exact byte identity used for deduplication.

    Args:
        left (PixelIdentity): The first identity.
        right (PixelIdentity): The second identity.

    Returns:
        bool:
            True if both are set, current-profile and equal in the compared fields.
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
