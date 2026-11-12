"""TODO."""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from biomero_schema.zarr import (
    SHALLOW_COLLECTION_MANIFEST,
    ShallowBindings,
    ShallowCollection,
    ShallowImageBinding,
    ShallowImageNode,
    ShallowLabelBinding,
    ShallowLabelNode,
    ShallowManifest,
)

from .pixel_identity import (
    PixelIdentityError,
)
from .output_declarations import references_omitted_files
from .ngff import (
    interchange_profile as node_profile, metadata_path, read_attrs,
)
from .filesystem import write_json
from .transaction import (
    begin, commit, recover, remove_rollback, rollback_path, sync_directory,
)


from .ngff import join_node_path, node_directory, safe_node_path, write_attrs
from .discovery import discover_ngff_nodes
from .evaluation import ReturnedZarrDecision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedShallowResult:
    """Committed shallow collection and optional storage measurements."""

    store_path: Path
    manifest: ShallowManifest
    bytes_before: int | None
    bytes_after: int | None


def _declared_image_dataset_directories(
    root: Path,
    image_node_path: str,
) -> tuple[Path, ...]:
    image_node = node_directory(root, image_node_path)
    attrs = read_attrs(image_node)
    multiscales = attrs.get("multiscales")
    if not isinstance(multiscales, list) or not multiscales:
        raise PixelIdentityError(
            f"NGFF image node has no multiscales metadata: {image_node_path}"
        )
    directories = []
    for multiscale in multiscales:
        datasets = (
            multiscale.get("datasets")
            if isinstance(multiscale, dict)
            else None
        )
        if not isinstance(datasets, list) or not datasets:
            raise PixelIdentityError(
                f"NGFF multiscales has no datasets: {image_node_path}"
            )
        for dataset in datasets:
            dataset_path = (
                dataset.get("path") if isinstance(dataset, dict) else None
            )
            if not isinstance(dataset_path, str):
                raise PixelIdentityError(
                    f"NGFF dataset path is missing: {image_node_path}"
                )
            dataset_path = safe_node_path(dataset_path)
            directory = node_directory(
                root,
                join_node_path(image_node_path, dataset_path),
            )
            try:
                directory.relative_to(root)
            except ValueError as exc:
                raise PixelIdentityError(
                    f"NGFF dataset escapes its store: {dataset_path}"
                ) from exc
            directories.append(directory)
    return tuple(dict.fromkeys(directories))


def _tree_size(root: Path) -> int:
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def normalize_returned_zarr(
    decision: ReturnedZarrDecision,
    workflow_id: UUID | str,
    *,
    replace=os.replace,
    measure_bytes: bool = False,
    before_commit=None,
) -> NormalizedShallowResult:
    """Transactionally omit verified duplicate arrays from a result store.

    Duplicate image arrays are atomically moved into a sibling rollback
    journal.  Labels and metadata stay in place, avoiding a costly copy of the
    retained side of a large Plate.  The manifest is committed only after all
    moves and metadata updates validate.  Any pre-commit failure restores the
    original attributes and moves every array back.  The canonical source is
    never modified.

    Exact byte measurement is disabled by default because recursively stating
    every file in a Plate can dominate the complete transaction on a mounted
    filesystem.  ``measure_bytes`` is intended for diagnostics and benchmarks,
    not the interactive import path.
    """
    root = _require_prunable_store(decision)
    nodes = discover_ngff_nodes(root)
    image_nodes = tuple(node for node in nodes if node.role == "image")
    label_nodes = tuple(node for node in nodes if node.role == "label")
    if not image_nodes:
        raise PixelIdentityError(
            "Returned stores require image nodes"
        )
    returned_by_path = {
        identity.node_path: identity
        for identity in decision.image_identities
    }
    if (decision.decision_basis == "pixel-comparison"
            and set(returned_by_path) != {node.node_path for node in image_nodes}):
        raise PixelIdentityError(
            "Returned identities no longer describe the discovered image nodes"
        )
    input_sources, interchange_profile = _canonical_sources_of_match(
        root, decision.matched_inputs[0], image_nodes)
    manifest = _build_shallow_manifest(
        decision, workflow_id, root, image_nodes, input_sources,
        interchange_profile, returned_by_path)

    inherited_label_paths = {
        component.logical_node_path
        for component in decision.label_components
        if component.source is not None
    }
    omitted_directories = _omitted_array_directories(
        root, image_nodes, inherited_label_paths)
    if references_omitted_files(root, omitted_directories):
        raise PixelIdentityError("RO-Crate inventories omitted arrays; retain full storage")
    top_level_omitted = _top_level_directories(root, omitted_directories)

    rollback = rollback_path(root, uuid4().hex)
    bytes_before = _tree_size(root) if measure_bytes else None
    if (root / SHALLOW_COLLECTION_MANIFEST).exists():
        raise PixelIdentityError(
            f"Returned store already has {SHALLOW_COLLECTION_MANIFEST}"
        )

    original_attrs = {
        metadata_path(node_directory(root, node.node_path)):
        metadata_path(node_directory(root, node.node_path)).read_bytes()
        for node in image_nodes
    }
    logger.info(f"Pruning {root}: moving {len(top_level_omitted)} array directories to {rollback}")
    try:
        bytes_after = _prune_transaction(
            root, rollback, manifest, image_nodes, label_nodes,
            top_level_omitted, omitted_directories, inherited_label_paths,
            original_attrs, replace=replace, measure_bytes=measure_bytes,
            before_commit=before_commit)
    except Exception:
        logger.info(f"Prune of {root} failed; rolling back")
        # Restore metadata before the arrays so readers never see valid
        # multiscales pointing at only a partially restored pyramid.
        recover(root)
        raise

    # Validation is the commit boundary. Cleanup only unlinks verified
    # duplicate pixels, so it must not attempt a rollback after a partial
    # filesystem deletion.
    remove_rollback(rollback)
    return NormalizedShallowResult(
        store_path=root,
        manifest=manifest,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def _require_prunable_store(decision: ReturnedZarrDecision) -> Path:
    """Return the store of an eligible single-input decision, or raise."""
    if not decision.eligible or len(decision.matched_inputs) != 1:
        raise PixelIdentityError("Returned Zarr is not eligible for shallowing")
    root = decision.store_path
    if not root.is_dir():
        raise PixelIdentityError(f"Returned Zarr does not exist: {root}")
    return root


def _canonical_sources_of_match(root, matched_input, image_nodes):
    """Return the canonical source of each image node and the shared profile."""
    if matched_input.plate_source is not None:
        input_sources = {
            image.image_node_path: image.source
            for image in matched_input.plate_source.images
        }
        interchange_profile = matched_input.plate_source.interchange_profile
    elif matched_input.source is not None:
        input_sources = {matched_input.source.node_path: matched_input.source}
        interchange_profile = matched_input.source.interchange_profile
    else:
        raise PixelIdentityError("Matched input has no canonical source")
    if interchange_profile != node_profile(root):
        raise PixelIdentityError("Canonical and returned Zarr profiles differ")
    if set(input_sources) != {node.node_path for node in image_nodes}:
        raise PixelIdentityError(
            "Matched input no longer describes every returned image node"
        )
    return input_sources, interchange_profile


def _label_components_by_image(decision, image_nodes):
    """Group the decision's label components under their unique parent image."""
    components_by_image = {node.node_path: [] for node in image_nodes}
    for component in decision.label_components:
        parents = [
            node.node_path for node in image_nodes
            if PurePosixPath(component.logical_node_path).is_relative_to(
                PurePosixPath(join_node_path(node.node_path, "labels"))
            )
        ]
        if len(parents) != 1:
            raise PixelIdentityError(
                f"Label has no unique parent image: {component.logical_node_path}"
            )
        components_by_image[parents[0]].append(component)
    return components_by_image


def _build_shallow_manifest(decision, workflow_id, root, image_nodes,
                            input_sources, interchange_profile, returned_by_path):
    """Describe the shallow collection: its graph nodes and their source bindings."""
    components_by_image = _label_components_by_image(decision, image_nodes)
    image_graph_nodes = tuple(
        ShallowImageNode(
            id=f"image-{index}",
            name=image_node.node_path,
            node_path=image_node.node_path,
        )
        for index, image_node in enumerate(image_nodes)
    )
    image_ids = {
        image.node_path: image.node_id for image in image_graph_nodes
    }
    label_records = tuple(
        (f"label-{index}", image_path, component)
        for index, (image_path, component) in enumerate(
            (image_node.node_path, component)
            for image_node in image_nodes
            for component in components_by_image[image_node.node_path]
        )
    )
    return ShallowManifest(
        workflow_id=UUID(str(workflow_id)),
        transfer_artifact=root.name,
        interchange_profile=interchange_profile,
        collection=ShallowCollection(
            name=root.name,
            images=image_graph_nodes,
            labels=tuple(
                ShallowLabelNode(
                    id=label_id,
                    name=component.logical_node_path,
                    node_path=component.logical_node_path,
                    source_image_id=image_ids[image_path],
                )
                for label_id, image_path, component in label_records
            ),
        ),
        bindings=ShallowBindings(
            images=tuple(
                ShallowImageBinding(
                    node_id=image_ids[image_node.node_path],
                    source=input_sources[image_node.node_path],
                    returned_pixel_identity=(
                        returned_by_path.get(image_node.node_path)
                    ),
                    decision_basis=decision.decision_basis,
                )
                for image_node in image_nodes
            ),
            labels=tuple(
                ShallowLabelBinding(
                    node_id=label_id,
                    component=component,
                )
                for label_id, _image_path, component in label_records
            ),
        ),
    )


def _omitted_array_directories(root, image_nodes, inherited_label_paths):
    """Return the image datasets and inherited label trees to move out of the store."""
    omitted = {
        directory
        for image_node in image_nodes
        for directory in _declared_image_dataset_directories(
            root,
            image_node.node_path,
        )
    }
    omitted.update(
        node_directory(root, label_path)
        for label_path in inherited_label_paths
    )
    return omitted


def _top_level_directories(root, directories):
    """Drop directories nested in another one, shallowest first.

    Moving whole Zarr array directories on one filesystem is metadata-only.
    Keeping only top-level directories means a future nested declaration
    cannot cause the same subtree to be moved twice.
    """
    top_level = []
    for directory in sorted(
        directories,
        key=lambda path: len(path.relative_to(root).parts),
    ):
        if not any(
            directory.is_relative_to(parent)
            for parent in top_level
        ):
            top_level.append(directory)
    return top_level


def _prune_transaction(root, rollback, manifest, image_nodes, label_nodes,
                       top_level_omitted, omitted_directories,
                       inherited_label_paths, original_attrs, *, replace,
                       measure_bytes, before_commit):
    """Move the arrays out, rewrite metadata, validate, then commit the journal.

    Returns the store size after pruning (None unless ``measure_bytes``).
    Raises on any failure before the commit; the caller rolls back.
    """
    rollback.mkdir()
    journal = begin(root, rollback, top_level_omitted, original_attrs)
    sync_directory(root.parent)
    _move_arrays_to_rollback(root, rollback, top_level_omitted, replace)
    _write_shallow_metadata(root, image_nodes, manifest)
    _validate_staged_result(root, manifest, label_nodes, omitted_directories,
                            inherited_label_paths)
    bytes_after = _tree_size(root) if measure_bytes else None
    if before_commit is not None:
        before_commit(manifest)
    commit(journal)
    logger.info(f"Committed prune of {root}")
    return bytes_after


def _move_arrays_to_rollback(root, rollback, directories, replace):
    """Move each omitted array directory into the rollback folder, keeping its relative path."""
    for directory in directories:
        if not directory.is_dir():
            raise PixelIdentityError(
                f"Returned array disappeared before normalization: "
                f"{directory}"
            )
        target = rollback / directory.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        replace(directory, target)
        sync_directory(directory.parent)
        sync_directory(target.parent)


def _write_shallow_metadata(root, image_nodes, manifest):
    """Point each image node at the shallow manifest and write the manifest."""
    for image_node in image_nodes:
        image_directory = node_directory(root, image_node.node_path)
        attrs = read_attrs(metadata_path(image_directory).parent)
        attrs.pop("multiscales", None)
        attrs["biomero"] = {
            "format": manifest.format,
            "manifest": SHALLOW_COLLECTION_MANIFEST,
            "workflowId": str(manifest.workflow_id),
        }
        write_attrs(image_directory, attrs)
    write_json(root / SHALLOW_COLLECTION_MANIFEST, manifest.to_dict())


def _validate_staged_result(root, manifest, label_nodes, omitted_directories,
                            inherited_label_paths):
    """Check the staged store: the manifest round-trips, labels and datasets are as planned."""
    written = json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")
    )
    if ShallowManifest.from_dict(written) != manifest:
        raise PixelIdentityError("Shallow manifest changed")
    for label in label_nodes:
        label_dir = node_directory(root, label.node_path)
        if label.node_path in inherited_label_paths:
            if label_dir.exists():
                raise PixelIdentityError(
                    f"Staged shallow result retained inherited label "
                    f"{label.node_path}"
                )
        elif not label_dir.is_dir():
            raise PixelIdentityError(
                f"Staged shallow result lost label node {label.node_path}"
            )
    for dataset in omitted_directories:
        if dataset.exists():
            raise PixelIdentityError(
                f"Shallow result retained image dataset {dataset}"
            )
