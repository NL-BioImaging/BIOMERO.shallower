"""Transactional pruning: move verified duplicate arrays out of a returned store.

`normalize_returned_zarr` plans the prune (nothing is touched), journals the
intent, moves the arrays into a sibling rollback folder, rewrites the image
metadata, validates the staged store, commits the journal and only then
deletes the rollback folder. A failure before the commit rolls everything back.
"""

from dataclasses import dataclass
import json
import logging
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

from .discovery import NgffNode, discover_ngff_nodes
from .evaluation import BASIS_PIXEL_COMPARISON, ReturnedZarrDecision
from .filesystem import resolve_move, sync_directory, write_json
from .ngff import (
    LABELS_GROUP_NAME, PixelIdentityError, interchange_profile as node_profile,
    join_node_path, metadata_path, node_directory, read_attrs, safe_node_path,
    write_attrs,
)
from .output_declarations import references_omitted_files
from .transaction import (
    begin, commit, recover, remove_rollback, rollback_path,
)

__all__ = ["NormalizedShallowResult", "PrunePlan", "normalize_returned_zarr"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedShallowResult:
    """Committed shallow collection and optional storage measurements."""

    store_path: Path
    manifest: ShallowManifest
    bytes_before: int | None
    bytes_after: int | None


@dataclass(frozen=True)
class PrunePlan:
    """Everything a prune does, decided before any file is touched."""

    root: Path
    rollback: Path
    manifest: ShallowManifest
    image_nodes: tuple[NgffNode, ...]
    label_nodes: tuple[NgffNode, ...]
    # Every image dataset and inherited label directory that must be gone afterwards.
    omitted_directories: frozenset[Path]
    # The subset that is actually moved: directories nested in another are dropped.
    moved_directories: tuple[Path, ...]
    inherited_label_paths: frozenset[str]
    # Original image-node attribute files and their bytes, restored on rollback.
    original_attrs: dict[Path, bytes]


def normalize_returned_zarr(
    decision: ReturnedZarrDecision,
    workflow_id: UUID | str,
    *,
    move=None,
    replace=None,
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

    ``move`` renames one directory (default ``os.replace``; tests inject
    failures). ``replace`` is the deprecated former name of ``move``, still
    accepted for existing callers.
    """
    move = resolve_move(move, replace)
    plan = _plan_prune(decision, workflow_id)
    bytes_before = _tree_size(plan.root) if measure_bytes else None
    logger.info(f"Pruning {plan.root}: moving {len(plan.moved_directories)} "
                f"array directories to {plan.rollback}")
    try:
        journal = _begin_prune(plan)
        bytes_after = _execute_prune(plan, journal, move=move,
                                     measure_bytes=measure_bytes,
                                     before_commit=before_commit)
    except Exception:
        logger.info(f"Prune of {plan.root} failed; rolling back")
        _roll_back(plan.root)
        raise
    # Validation is the commit boundary. Cleanup only unlinks verified
    # duplicate pixels, so it must not attempt a rollback after a partial
    # filesystem deletion.
    remove_rollback(plan.rollback)
    return NormalizedShallowResult(
        store_path=plan.root,
        manifest=plan.manifest,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
    )


def _plan_prune(decision, workflow_id):
    """Check the decision against the store and plan the prune without touching it."""
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
    if (decision.decision_basis == BASIS_PIXEL_COMPARISON
            and set(returned_by_path) != {node.node_path for node in image_nodes}):
        raise PixelIdentityError(
            "Returned identities no longer describe the discovered image nodes"
        )
    input_sources, interchange_profile = _canonical_sources_of_match(
        root, decision.matched_inputs[0], image_nodes)
    manifest = _build_shallow_manifest(
        decision, workflow_id, root, image_nodes, input_sources,
        interchange_profile, returned_by_path)

    inherited_label_paths = frozenset(
        component.logical_node_path
        for component in decision.label_components
        if component.source is not None
    )
    omitted_directories = _omitted_array_directories(
        root, image_nodes, inherited_label_paths)
    if references_omitted_files(root, omitted_directories):
        raise PixelIdentityError("RO-Crate inventories omitted arrays; retain full storage")
    if (root / SHALLOW_COLLECTION_MANIFEST).exists():
        raise PixelIdentityError(
            f"Returned store already has {SHALLOW_COLLECTION_MANIFEST}"
        )
    original_attrs = {
        metadata_path(node_directory(root, node.node_path)):
        metadata_path(node_directory(root, node.node_path)).read_bytes()
        for node in image_nodes
    }
    return PrunePlan(
        root=root,
        rollback=rollback_path(root, uuid4().hex),
        manifest=manifest,
        image_nodes=image_nodes,
        label_nodes=label_nodes,
        omitted_directories=frozenset(omitted_directories),
        moved_directories=tuple(_top_level_directories(root, omitted_directories)),
        inherited_label_paths=inherited_label_paths,
        original_attrs=original_attrs,
    )


def _begin_prune(plan):
    """Create the rollback folder and journal the intent; return the journal."""
    plan.rollback.mkdir()
    journal = begin(plan.root, plan.rollback, plan.moved_directories, plan.original_attrs)
    sync_directory(plan.root.parent)
    return journal


def _execute_prune(plan, journal, *, move, measure_bytes, before_commit):
    """Move the arrays out, rewrite metadata, validate, then commit the journal.

    Returns the store size after pruning (None unless ``measure_bytes``).
    Raises on any failure before the commit; the caller rolls back.
    """
    _move_arrays_to_rollback(plan.root, plan.rollback, plan.moved_directories, move)
    _write_shallow_metadata(plan.root, plan.image_nodes, plan.manifest)
    _validate_staged_result(plan)
    bytes_after = _tree_size(plan.root) if measure_bytes else None
    if before_commit is not None:
        before_commit(plan.manifest)
    commit(journal)
    logger.info(f"Committed prune of {plan.root}")
    return bytes_after


def _roll_back(root):
    """Undo a failed prune; a failure to do so is logged and never masks the prune error.

    `recover` restores the arrays, then the saved attributes, then drops the
    shallow files. If it fails too, the next `normalize` or `recover` retries.
    """
    try:
        recover(root)
    except Exception:
        logger.exception(f"Rollback of {root} failed; run recover to retry")



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
                PurePosixPath(join_node_path(node.node_path, LABELS_GROUP_NAME))
            )
        ]
        if len(parents) != 1:
            raise PixelIdentityError(
                f"Label has no unique parent image: {component.logical_node_path}"
            )
        components_by_image[parents[0]].append(component)
    return components_by_image


def _label_records(components_by_image, image_nodes):
    """Number every label component as ``(label id, parent image path, component)``."""
    return tuple(
        (f"label-{index}", image_path, component)
        for index, (image_path, component) in enumerate(
            (image_node.node_path, component)
            for image_node in image_nodes
            for component in components_by_image[image_node.node_path]
        )
    )


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
    label_records = _label_records(components_by_image, image_nodes)
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


def _move_arrays_to_rollback(root, rollback, directories, move):
    """Move each omitted array directory into the rollback folder, keeping its relative path."""
    for directory in directories:
        if not directory.is_dir():
            raise PixelIdentityError(
                f"Returned array disappeared before normalization: "
                f"{directory}"
            )
        target = rollback / directory.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        move(directory, target)
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


def _validate_staged_result(plan):
    """Check the staged store: the manifest round-trips, labels and datasets are as planned."""
    root = plan.root
    written = json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")
    )
    if ShallowManifest.from_dict(written) != plan.manifest:
        raise PixelIdentityError("Shallow manifest changed")
    for label in plan.label_nodes:
        label_dir = node_directory(root, label.node_path)
        if label.node_path in plan.inherited_label_paths:
            if label_dir.exists():
                raise PixelIdentityError(
                    f"Staged shallow result retained inherited label "
                    f"{label.node_path}"
                )
        elif not label_dir.is_dir():
            raise PixelIdentityError(
                f"Staged shallow result lost label node {label.node_path}"
            )
    for dataset in plan.omitted_directories:
        if dataset.exists():
            raise PixelIdentityError(
                f"Shallow result retained image dataset {dataset}"
            )


def _declared_image_dataset_directories(
    root: Path,
    image_node_path: str,
) -> tuple[Path, ...]:
    """Return the dataset (pyramid level) directories an image node declares, validated."""
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
    """Total size in bytes of the regular files below ``root`` (diagnostics only: it stats everything)."""
    return sum(
        path.stat().st_size
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
