"""TODO."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
import json
import logging
from pathlib import Path
from typing import Literal

from biomero_schema.zarr import (
    CanonicalInput,
    CanonicalInputManifest,
    PixelIdentity,
    TRANSFER_INPUT_MARKER,
    ZarrLabelComponent,
)

from .pixel_identity import (
    IsccBioIdentityProvider,
    PixelIdentityError,
    pixel_identities_match,
    read_zarr_semantic_guard,
    read_zarr_comparison_levels,
    comparison_level_matches,
)
from .output_declarations import declared_label_input, same_input_artifact
from .ngff import (
    interchange_profile as node_profile,
)


from .discovery import NgffNode, discover_ngff_nodes
from .ngff import node_directory
from .registration import resolve_managed_source_path

logger = logging.getLogger(__name__)


# Decision outcomes of ReturnedZarrDecision.
OUTCOME_ELIGIBLE = "eligible"


OUTCOME_KEEP_FULL = "keep-full"


OUTCOME_SKIP_PASSTHROUGH = "skip-passthrough"


# Keep-full reasons that signal a failure, an unsupported layout or an
# untrustworthy input. A reason may carry a ": <detail>" suffix.
REASON_NO_CANONICAL_INPUT_SNAPSHOT = "no-canonical-input-snapshot"


REASON_INVALID_TRANSFER_INPUT_MARKER = "invalid-transfer-input-marker"


REASON_UNTRUSTED_TRANSFER_INPUT_MARKER = "untrusted-transfer-input-marker"


REASON_AMBIGUOUS_TRANSFER_ARTIFACT = "ambiguous-transfer-artifact"


REASON_AMBIGUOUS_INPUT_IDENTITY = "ambiguous-input-identity"


REASON_UNSUPPORTED_CANONICAL_PROFILE = "unsupported-canonical-profile"


REASON_UNSUPPORTED_NGFF = "unsupported-ngff"


REASON_IDENTITY_UNAVAILABLE = "identity-unavailable"


REASON_LABEL_IDENTITY_UNAVAILABLE = "label-identity-unavailable"


REASON_NORMALIZATION_FAILED = "normalization-failed"


# Keep-full reasons that are ordinary outcomes: nothing to reuse or no match.
REASON_NO_IMAGE_NODES = "no-image-nodes"


REASON_IMAGE_LAYOUT_MISMATCH = "image-layout-mismatch"


REASON_IMAGE_SEMANTICS_MISMATCH = "image-semantics-mismatch"


REASON_PIXELS_CHANGED = "pixels-changed"


REASON_NO_INPUT_IDENTITY_MATCH = "no-input-identity-match"


FAILURE_REASON_PREFIXES = (
    REASON_NO_CANONICAL_INPUT_SNAPSHOT,
    REASON_INVALID_TRANSFER_INPUT_MARKER,
    REASON_UNTRUSTED_TRANSFER_INPUT_MARKER,
    REASON_AMBIGUOUS_TRANSFER_ARTIFACT,
    REASON_AMBIGUOUS_INPUT_IDENTITY,
    REASON_UNSUPPORTED_CANONICAL_PROFILE,
    REASON_UNSUPPORTED_NGFF,
    REASON_IDENTITY_UNAVAILABLE,
    REASON_LABEL_IDENTITY_UNAVAILABLE,
    REASON_NORMALIZATION_FAILED,
)


def is_failure_reason(reason: str) -> bool:
    """Whether a keep-full reason reports a failure, unsupported layout or untrusted input.

    Reasons such as ``pixels-changed`` or ``no-input-identity-match`` are
    expected outcomes and return False.
    """
    return reason.startswith(FAILURE_REASON_PREFIXES)


@dataclass(frozen=True)
class ReturnedZarrDecision:
    """Keep-only result of comparing one returned store with workflow inputs."""

    store_path: Path
    outcome: Literal["eligible", "keep-full", "skip-passthrough"]
    reason: str
    image_identities: tuple[PixelIdentity, ...] = ()
    label_identities: tuple[PixelIdentity, ...] = ()
    label_components: tuple[ZarrLabelComponent, ...] = ()
    label_node_paths: tuple[str, ...] = ()
    matched_inputs: tuple[CanonicalInput, ...] = ()
    decision_basis: Literal["pixel-comparison", "ro-crate-output", "workflow-setting"] = "pixel-comparison"
    refreshed_inputs: tuple[CanonicalInput, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.outcome == OUTCOME_ELIGIBLE

    @property
    def unchanged_passthrough(self) -> bool:
        return self.outcome == OUTCOME_SKIP_PASSTHROUGH


def _identity_for_node(
    root: Path,
    node: NgffNode,
    identity_provider: IsccBioIdentityProvider,
    *, dataset_path=None,
) -> PixelIdentity:
    guard = read_zarr_semantic_guard(root, node.node_path)
    return identity_provider.generate(
        root,
        node_path=node.node_path,
        role=node.role,
        shape=guard.shape,
        dtype=guard.dtype,
        axes=guard.axes,
        coordinate_transformations=guard.coordinate_transformations,
        **({"dataset_path": dataset_path} if dataset_path is not None else {}),
    )


def _identities_for_nodes(
    root: Path,
    nodes: tuple[NgffNode, ...],
    identity_provider: IsccBioIdentityProvider,
    *,
    max_workers: int = 1,
) -> tuple[PixelIdentity, ...]:
    """Generate identities in node order with optional bounded concurrency."""
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise ValueError("Identity workers must be a positive integer")
    if max_workers < 1:
        raise ValueError("Identity workers must be a positive integer")
    generate = partial(_identity_for_node, root, identity_provider=identity_provider)
    if max_workers == 1 or len(nodes) < 2:
        return tuple(generate(node) for node in nodes)
    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(nodes)),
        thread_name_prefix="biomero-iscc",
    ) as executor:
        # executor.map preserves the discovered node order even when workers
        # finish out of order, keeping manifests and comparisons deterministic.
        return tuple(executor.map(generate, nodes))


def _input_sources(item):
    return ({image.image_node_path: image.source for image in item.plate_source.images}
            if item.plate_source else {item.source.node_path: item.source})


def _with_sources(item, sources):
    if item.plate_source:
        plate = item.plate_source.model_copy(update={"images": tuple(
            image.model_copy(update={"source": sources[image.image_node_path]})
            for image in item.plate_source.images)})
        return item.model_copy(update={"plate_source": plate})
    return item.model_copy(update={"source": sources[item.source.node_path]})


def _source_path(source, storage_roots):
    if storage_roots and source.storage_root in storage_roots:
        return resolve_managed_source_path(source, storage_roots)
    return None


def _guard_matches_source(guard, source):
    identity = source.pixel_identity
    metadata = source.image_metadata
    if identity is not None:
        return (guard.shape == identity.shape and guard.dtype == identity.dtype
                and guard.axes == identity.axes
                and guard.coordinate_transformations == identity.coordinate_transformations)
    if metadata is not None:
        return (guard.shape == tuple(metadata.get("shape", ()))
                and guard.dtype == metadata.get("dtype")
                and guard.axes == tuple(metadata.get("axes", ()))
                and guard.coordinate_transformations == tuple(metadata.get("coordinateTransformations", ())))
    return False


def _log_decision(decision: ReturnedZarrDecision) -> ReturnedZarrDecision:
    """Log the outcome, reason and basis of a decision, and return it unchanged."""
    logger.debug(f"Decision for {decision.store_path}: outcome={decision.outcome}, "
                 f"reason={decision.reason}, basis={decision.decision_basis}, "
                 f"matched inputs={len(decision.matched_inputs)}")
    if decision.outcome == OUTCOME_KEEP_FULL and is_failure_reason(decision.reason):
        logger.warning(f"Keeping {decision.store_path} full: {decision.reason}")
    return decision


def _keep_full(store_path: Path, reason: str, **fields) -> ReturnedZarrDecision:
    """Return (and log) a keep-full decision; ``fields`` are extra decision attributes."""
    return _log_decision(ReturnedZarrDecision(
        store_path=store_path, outcome=OUTCOME_KEEP_FULL, reason=reason, **fields))


def _sources_by_node_path(item: CanonicalInput) -> dict:
    """Map each image node path of a canonical input to its canonical source."""
    if item.plate_source is not None:
        return {
            image.image_node_path: image.source
            for image in item.plate_source.images
        }
    if item.source is not None:
        return {item.source.node_path: item.source}
    return {}


def _has_interchange_profile(item: CanonicalInput, profile: str) -> bool:
    """Whether the input has sources and all of them use ``profile``."""
    sources = _sources_by_node_path(item)
    return bool(sources) and all(
        source.interchange_profile == profile for source in sources.values()
    )


def _read_transfer_input_marker(root: Path, canonical_inputs: CanonicalInputManifest):
    """Return ``(marked_input, rejection_reason)`` for the store's optional input marker."""
    marker_path = root / TRANSFER_INPUT_MARKER
    if not marker_path.is_file():
        return None, None
    try:
        marked_input = CanonicalInput.from_dict(json.loads(
            marker_path.read_text(encoding="utf-8")
        ))
    except (OSError, json.JSONDecodeError, ValueError):
        return None, REASON_INVALID_TRANSFER_INPUT_MARKER
    if marked_input not in canonical_inputs.inputs:
        return None, REASON_UNTRUSTED_TRANSFER_INPUT_MARKER
    return marked_input, None


def _candidates_matching_image_semantics(candidates, guards, storage_roots):
    """Keep inputs whose canonical sources have the returned images' shape, dtype and axes.

    A source without recorded identity or metadata gets its metadata read from
    the managed canonical store when that store is available.
    """
    matching = []
    for item in candidates:
        sources = dict(_sources_by_node_path(item))
        for path, source in sources.items():
            if source.pixel_identity is None and source.image_metadata is None:
                try:
                    source_root = _source_path(source, storage_roots)
                    if source_root is None:
                        continue
                    guard = read_zarr_semantic_guard(source_root, path)
                    source = source.model_copy(update={"image_metadata": {
                        "shape": list(guard.shape), "dtype": guard.dtype,
                        "axes": list(guard.axes),
                        "coordinateTransformations": list(guard.coordinate_transformations)}})
                    sources[path] = source
                except (OSError, ValueError):
                    continue
        if all(_guard_matches_source(guards[path], source) for path, source in sources.items()):
            matching.append(_with_sources(item, sources))
    return tuple(matching)


def _select_decision_basis(root, canonical_inputs, candidates, label_paths, marked_input):
    """Return ``(basis, selected_input)``; the input is None when pixels must be compared.

    A declared RO-Crate output or the workflow setting "original pixels not
    required" can bind the result to one input without comparing pixels.
    """
    declared_input = declared_label_input(root, canonical_inputs, candidates, label_paths)
    if declared_input is not None:
        return "ro-crate-output", declared_input
    if canonical_inputs.original_pixels_not_required:
        # An explicit run/input filename binding is necessary for multiple inputs.
        choices = tuple(item for item in candidates
                        if same_input_artifact(item.transfer_artifact, root.name))
        if marked_input is not None:
            choices = candidates
        if len(choices) == 1:
            return "workflow-setting", choices[0]
    return "pixel-comparison", None


def _changed_inputs(refreshed, canonical_inputs):
    """Return the inputs whose sources were refreshed during this evaluation."""
    original_inputs = {item.ordinal: item for item in canonical_inputs.inputs}
    return tuple(item for ordinal, item in refreshed.items()
                 if item != original_inputs[ordinal])


def _compare_node_pixels(root, node, active, provider, storage_roots):
    """Compare one returned image node with each active input.

    Returns ``(node_path, {input ordinal: (source, returned identity, matches)})``.
    The smallest pyramid level common to both sides is hashed, not the full image.
    """
    path = node.node_path
    returned_levels = read_zarr_comparison_levels(root, path)
    measured = {}
    results = {}
    for item in active:
        source = _sources_by_node_path(item)[path]
        identity = source.pixel_identity
        level = next((level for level in returned_levels
                      if comparison_level_matches(level, identity)), None)
        if level is None:
            source_root = _source_path(source, storage_roots)
            if source_root is None:
                continue
            source_levels = read_zarr_comparison_levels(source_root, path)
            common = next(((left, right) for left in returned_levels for right in source_levels
                           if left.shape == right.shape and left.dtype == right.dtype
                           and left.axes == right.axes
                           and left.coordinate_transformations == right.coordinate_transformations), None)
            if common is None:
                continue
            level, source_level = common
            identity = _identity_for_node(source_root, node, provider,
                                          dataset_path=source_level.dataset_path)
            source = source.model_copy(update={"pixel_identity": identity,
                                               "canonical_pixel_verified": True})
        if level.dataset_path not in measured:
            measured[level.dataset_path] = _identity_for_node(root, node, provider,
                                                              dataset_path=level.dataset_path)
        returned = measured[level.dataset_path]
        results[item.ordinal] = (source, returned, pixel_identities_match(returned, identity))
    return path, results


def _inputs_matching_all_images(root, image_nodes, candidates, refreshed,
                                returned_by_candidate, *, identity_workers,
                                provider, storage_roots):
    """Return the candidates whose pixels match every returned image node.

    ``refreshed`` and ``returned_by_candidate`` are updated in place, so they
    stay usable if a later batch raises.
    """
    active = candidates
    # Submit one bounded batch at a time; failed candidates cannot cause
    # the remainder of a large Plate to be queued for needless reads.
    with ThreadPoolExecutor(max_workers=min(identity_workers, len(image_nodes))) as executor:
        for offset in range(0, len(image_nodes), identity_workers):
            batch = image_nodes[offset:offset + identity_workers]
            results = list(executor.map(
                lambda node: _compare_node_pixels(root, node, active, provider, storage_roots),
                batch))
            surviving = {item.ordinal for item in active}
            for path, field_results in results:
                surviving.intersection_update(
                    ordinal for ordinal, value in field_results.items() if value[2])
                for ordinal, (source, returned, _) in field_results.items():
                    updated = dict(_sources_by_node_path(refreshed[ordinal]))
                    updated[path] = source
                    refreshed[ordinal] = _with_sources(refreshed[ordinal], updated)
                    returned_by_candidate[ordinal][path] = returned
            active = tuple(refreshed[item.ordinal] for item in active
                           if item.ordinal in surviving)
            if not active:
                break
    return active


def _label_components_of_match(matched_input, label_identities):
    """Pair each returned label with the input label it is identical to, if any."""
    if matched_input.plate_source is not None:
        input_labels = {
            label.logical_node_path: label
            for image in matched_input.plate_source.images
            for label in image.labels
        }
    else:
        input_labels = {
            label.logical_node_path: label
            for label in matched_input.labels
        }
    label_components = []
    for identity in label_identities:
        inherited = input_labels.get(identity.node_path)
        source = None
        if inherited is not None and pixel_identities_match(
            identity,
            inherited.pixel_identity,
        ):
            source = inherited.source
        label_components.append(ZarrLabelComponent(
            logical_node_path=identity.node_path,
            pixel_identity=identity,
            source=source,
        ))
    return tuple(label_components)


def evaluate_returned_zarr(
    zarr_root: str | Path,
    canonical_inputs: CanonicalInputManifest | None,
    *,
    identity_provider: IsccBioIdentityProvider | None = None,
    identity_workers: int = 1,
    storage_roots: dict[str, Path] | None = None,
) -> ReturnedZarrDecision:
    """Compare one returned image store with its workflow-scoped inputs.

    The function only reports eligibility.  It never changes the returned
    store, which makes failures and unsupported cases unconditionally fail
    open to ``keep-full``.  Every decision is logged with its reason.
    """
    if (
        isinstance(identity_workers, bool)
        or not isinstance(identity_workers, int)
        or identity_workers < 1
    ):
        raise ValueError("Identity workers must be a positive integer")
    root = Path(zarr_root)
    input_count = len(canonical_inputs.inputs) if canonical_inputs else 0
    logger.debug(f"Evaluating {root} against {input_count} canonical inputs "
                 f"with {identity_workers} identity workers")
    if canonical_inputs is None or not canonical_inputs.inputs:
        return _keep_full(root, REASON_NO_CANONICAL_INPUT_SNAPSHOT)

    marked_input, marker_rejection = _read_transfer_input_marker(root, canonical_inputs)
    if marker_rejection is not None:
        return _keep_full(root, marker_rejection)

    # Resolve explicit input bindings before filtering formats: a marker or
    # artifact name must never silently bind to a different canonical source.
    artifact_matches = tuple(
        item for item in canonical_inputs.inputs
        if item.transfer_artifact == root.name
    )
    if marked_input is not None:
        candidates = (marked_input,)
    elif len(artifact_matches) > 1:
        return _keep_full(root, REASON_AMBIGUOUS_TRANSFER_ARTIFACT)
    else:
        candidates = artifact_matches or canonical_inputs.inputs

    try:
        profile = node_profile(root)
        candidates = tuple(item for item in candidates
                           if _has_interchange_profile(item, profile))
        if not candidates:
            # Root metadata and the canonical snapshot suffice. Avoid opening
            # every Plate field, initializing readers or hashing any pixels.
            return _keep_full(root, REASON_UNSUPPORTED_CANONICAL_PROFILE)
        nodes = discover_ngff_nodes(root)
        if any(node_profile(node_directory(root, node.node_path)) != profile
               for node in nodes):
            raise PixelIdentityError("Mixed Zarr formats in returned image/label nodes")
    except PixelIdentityError as exc:
        return _keep_full(root, f"{REASON_UNSUPPORTED_NGFF}: {exc}")

    image_nodes = tuple(node for node in nodes if node.role == "image")
    label_paths = tuple(node.node_path for node in nodes if node.role == "label")
    if not image_nodes:
        return _keep_full(root, REASON_NO_IMAGE_NODES, label_node_paths=label_paths)
    provider = identity_provider or IsccBioIdentityProvider()
    image_paths = {node.node_path for node in image_nodes}
    candidates = tuple(item for item in candidates
                       if set(_sources_by_node_path(item)) == image_paths)
    if not candidates:
        return _keep_full(root, REASON_IMAGE_LAYOUT_MISMATCH, label_node_paths=label_paths)
    try:
        guards = {node.node_path: read_zarr_semantic_guard(root, node.node_path)
                  for node in image_nodes}
    except PixelIdentityError as exc:
        return _keep_full(root, f"{REASON_UNSUPPORTED_NGFF}: {exc}", label_node_paths=label_paths)
    candidates = _candidates_matching_image_semantics(candidates, guards, storage_roots)
    if not candidates:
        return _keep_full(root, REASON_IMAGE_SEMANTICS_MISMATCH, label_node_paths=label_paths)

    basis, selected = _select_decision_basis(
        root, canonical_inputs, candidates, label_paths, marked_input)
    refreshed = {item.ordinal: item for item in candidates}
    returned_by_candidate = {item.ordinal: {} for item in candidates}
    returned_identities = ()

    if selected is not None:
        matches = (selected,)
        reason = basis
    else:
        try:
            matching_inputs = _inputs_matching_all_images(
                root, image_nodes, candidates, refreshed, returned_by_candidate,
                identity_workers=identity_workers, provider=provider,
                storage_roots=storage_roots)
            matches = matching_inputs if len(matching_inputs) == 1 else ()
            reason = (REASON_AMBIGUOUS_INPUT_IDENTITY if len(matching_inputs) > 1 else
                      REASON_PIXELS_CHANGED if marked_input is not None or artifact_matches else
                      REASON_NO_INPUT_IDENTITY_MATCH)
            if matches:
                returned_identities = tuple(returned_by_candidate[matches[0].ordinal][node.node_path]
                                            for node in image_nodes)
        except (PixelIdentityError, OSError, ValueError) as exc:
            return _keep_full(root, f"{REASON_IDENTITY_UNAVAILABLE}: {exc}",
                              label_node_paths=label_paths,
                              refreshed_inputs=_changed_inputs(refreshed, canonical_inputs))
    refreshed_inputs = _changed_inputs(refreshed, canonical_inputs)
    if not matches:
        return _keep_full(root, reason, image_identities=returned_identities,
                          label_node_paths=label_paths, refreshed_inputs=refreshed_inputs)

    # Identity controls pixel storage, never whether the returned object is
    # a result. Label-free results can reference canonical pixels as well.
    matched_input = matches[0]
    if basis == "pixel-comparison":
        reason = "input-image-unchanged" if len(image_nodes) == 1 else "input-plate-unchanged"
    if any(source.interchange_profile != profile
           for source in _sources_by_node_path(matched_input).values()):
        return _keep_full(root, REASON_UNSUPPORTED_CANONICAL_PROFILE,
                          image_identities=returned_identities,
                          label_node_paths=label_paths, refreshed_inputs=refreshed_inputs)

    try:
        label_identities = _identities_for_nodes(
            root,
            tuple(node for node in nodes if node.role == "label"),
            provider,
            max_workers=identity_workers,
        )
    except PixelIdentityError as exc:
        return _keep_full(root, f"{REASON_LABEL_IDENTITY_UNAVAILABLE}: {exc}",
                          image_identities=returned_identities,
                          label_node_paths=label_paths, refreshed_inputs=refreshed_inputs)

    return _log_decision(ReturnedZarrDecision(
        store_path=root,
        outcome=OUTCOME_ELIGIBLE,
        reason=reason,
        image_identities=returned_identities,
        label_identities=label_identities,
        label_components=_label_components_of_match(matched_input, label_identities),
        label_node_paths=label_paths,
        matched_inputs=matches,
        decision_basis=basis,
        refreshed_inputs=refreshed_inputs,
    ))


def remove_transfer_input_marker(zarr_root: str | Path) -> bool:
    """Remove the temporary input binding before managed result storage."""
    marker = Path(zarr_root) / TRANSFER_INPUT_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        return False
    return True
