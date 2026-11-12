"""Decide whether a returned store's pixels may be replaced by canonical references.

Evaluation only reads: it never deletes, rewrites or registers anything, so a
caller can always keep every result full when discovery or identity matching
is incomplete. Each decision is logged with its reason.

The steps of `evaluate_returned_zarr` raise `_KeepFull` with the reason when
they cannot go on; the public function turns it into a keep-full decision.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
import json
import logging
from pathlib import Path
from typing import Literal, get_args

from biomero_schema.zarr import (
    CanonicalInput,
    CanonicalInputManifest,
    PixelIdentity,
    TRANSFER_INPUT_MARKER,
    ZarrLabelComponent,
)

from .discovery import NgffNode, discover_ngff_nodes
from .ngff import interchange_profile as node_profile, node_directory
from .output_declarations import declared_label_input, same_input_artifact
from .pixel_identity import (
    IsccBioIdentityProvider,
    PixelIdentityError,
    comparison_level_matches,
    pixel_identities_match,
    read_zarr_comparison_levels,
    read_zarr_semantic_guard,
)
from .registration import resolve_managed_source_path

__all__ = [
    "BASIS_PIXEL_COMPARISON",
    "BASIS_RO_CRATE_OUTPUT",
    "BASIS_WORKFLOW_SETTING",
    "FAILURE_REASON_PREFIXES",
    "OUTCOME_ELIGIBLE",
    "OUTCOME_KEEP_FULL",
    "OUTCOME_SKIP_PASSTHROUGH",
    "ReturnedZarrDecision",
    "evaluate_returned_zarr",
    "is_failure_reason",
    "remove_transfer_input_marker",
]

logger = logging.getLogger(__name__)

# Decision outcomes and bases are defined once, as types; the constants name their values.
Outcome = Literal["eligible", "keep-full", "skip-passthrough"]
OUTCOME_ELIGIBLE, OUTCOME_KEEP_FULL, OUTCOME_SKIP_PASSTHROUGH = get_args(Outcome)
DecisionBasis = Literal["pixel-comparison", "ro-crate-output", "workflow-setting"]
BASIS_PIXEL_COMPARISON, BASIS_RO_CRATE_OUTPUT, BASIS_WORKFLOW_SETTING = get_args(DecisionBasis)

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
# Reasons of an eligible decision that no pixel comparison was needed for.
REASON_INPUT_IMAGE_UNCHANGED = "input-image-unchanged"
REASON_INPUT_PLATE_UNCHANGED = "input-plate-unchanged"
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
    outcome: Outcome
    reason: str
    image_identities: tuple[PixelIdentity, ...] = ()
    label_identities: tuple[PixelIdentity, ...] = ()
    label_components: tuple[ZarrLabelComponent, ...] = ()
    label_node_paths: tuple[str, ...] = ()
    matched_inputs: tuple[CanonicalInput, ...] = ()
    decision_basis: DecisionBasis = BASIS_PIXEL_COMPARISON
    refreshed_inputs: tuple[CanonicalInput, ...] = ()

    @property
    def eligible(self) -> bool:
        """Whether the store may be pruned."""
        return self.outcome == OUTCOME_ELIGIBLE

    @property
    def unchanged_passthrough(self) -> bool:
        """Whether the store is an unchanged passthrough of its input."""
        return self.outcome == OUTCOME_SKIP_PASSTHROUGH


class _KeepFull(Exception):
    """A step cannot continue: keep the store full for ``reason``.

    ``fields`` are extra `ReturnedZarrDecision` attributes to record.
    """

    def __init__(self, reason: str, **fields):
        super().__init__(reason)
        self.reason = reason
        self.fields = fields


@dataclass(frozen=True)
class _StoreView:
    """What the returned store looks like, and the inputs of the same Zarr profile."""

    profile: str
    nodes: tuple[NgffNode, ...]
    candidates: tuple[CanonicalInput, ...]

    @property
    def image_nodes(self) -> tuple[NgffNode, ...]:
        return tuple(node for node in self.nodes if node.role == "image")

    @property
    def label_nodes(self) -> tuple[NgffNode, ...]:
        return tuple(node for node in self.nodes if node.role == "label")

    @property
    def label_paths(self) -> tuple[str, ...]:
        return tuple(node.node_path for node in self.label_nodes)


@dataclass(frozen=True)
class _InputMatch:
    """The one canonical input the store matches, and how that was established."""

    basis: DecisionBasis
    matched_input: CanonicalInput
    reason: str
    image_identities: tuple[PixelIdentity, ...]
    refreshed_inputs: tuple[CanonicalInput, ...]


def _log_decision(decision: ReturnedZarrDecision) -> ReturnedZarrDecision:
    """Log the outcome, reason and basis of a decision, and return it unchanged."""
    logger.debug(f"Decision for {decision.store_path}: outcome={decision.outcome}, "
                 f"reason={decision.reason}, basis={decision.decision_basis}, "
                 f"matched inputs={len(decision.matched_inputs)}")
    if decision.outcome == OUTCOME_KEEP_FULL and is_failure_reason(decision.reason):
        logger.warning(f"Keeping {decision.store_path} full: {decision.reason}")
    return decision


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
    try:
        decision = _evaluate(root, canonical_inputs, identity_provider,
                             identity_workers, storage_roots)
    except _KeepFull as keep:
        decision = ReturnedZarrDecision(
            store_path=root, outcome=OUTCOME_KEEP_FULL, reason=keep.reason, **keep.fields)
    return _log_decision(decision)


def _evaluate(root, canonical_inputs, identity_provider, identity_workers, storage_roots):
    """Run the evaluation steps in order; any step may raise `_KeepFull`."""
    if canonical_inputs is None or not canonical_inputs.inputs:
        raise _KeepFull(REASON_NO_CANONICAL_INPUT_SNAPSHOT)
    marked_input = _read_transfer_input_marker(root, canonical_inputs)
    candidates, bound_by_name = _select_candidates(root, canonical_inputs, marked_input)
    store = _read_store(root, candidates)
    provider = identity_provider or IsccBioIdentityProvider()
    store_candidates = _candidates_matching_store(root, store, storage_roots)
    match = _match_inputs(root, canonical_inputs, store, store_candidates, marked_input,
                          bound_by_name, provider, identity_workers, storage_roots)
    return _eligible_decision(root, store, match, provider, identity_workers)


def _read_transfer_input_marker(root, canonical_inputs):
    """Return the store's optional input marker, or raise `_KeepFull` if it cannot be trusted."""
    marker_path = root / TRANSFER_INPUT_MARKER
    if not marker_path.is_file():
        return None
    try:
        marked_input = CanonicalInput.from_dict(json.loads(
            marker_path.read_text(encoding="utf-8")
        ))
    except (OSError, json.JSONDecodeError, ValueError):
        raise _KeepFull(REASON_INVALID_TRANSFER_INPUT_MARKER)
    if marked_input not in canonical_inputs.inputs:
        raise _KeepFull(REASON_UNTRUSTED_TRANSFER_INPUT_MARKER)
    return marked_input


def _select_candidates(root, canonical_inputs, marked_input):
    """Return ``(candidate inputs, bound_by_name)`` for the store.

    Explicit input bindings are resolved before filtering formats: a marker or
    artifact name must never silently bind to a different canonical source.
    ``bound_by_name`` says whether the store is bound to an input by its marker
    or artifact name, as opposed to being compared with every input.
    """
    artifact_matches = tuple(
        item for item in canonical_inputs.inputs
        if item.transfer_artifact == root.name
    )
    if marked_input is not None:
        return (marked_input,), True
    if len(artifact_matches) > 1:
        raise _KeepFull(REASON_AMBIGUOUS_TRANSFER_ARTIFACT)
    return (artifact_matches or canonical_inputs.inputs), bool(artifact_matches)


def _read_store(root, candidates):
    """Read the store's profile and nodes; keep the candidates of that profile."""
    try:
        profile = node_profile(root)
        candidates = tuple(item for item in candidates
                           if _has_interchange_profile(item, profile))
        if not candidates:
            # Root metadata and the canonical snapshot suffice. Avoid opening
            # every Plate field, initializing readers or hashing any pixels.
            raise _KeepFull(REASON_UNSUPPORTED_CANONICAL_PROFILE)
        nodes = discover_ngff_nodes(root)
        if any(node_profile(node_directory(root, node.node_path)) != profile
               for node in nodes):
            raise PixelIdentityError("Mixed Zarr formats in returned image/label nodes")
    except PixelIdentityError as exc:
        raise _KeepFull(f"{REASON_UNSUPPORTED_NGFF}: {exc}") from exc
    store = _StoreView(profile=profile, nodes=nodes, candidates=candidates)
    if not store.image_nodes:
        raise _KeepFull(REASON_NO_IMAGE_NODES, label_node_paths=store.label_paths)
    return store


def _candidates_matching_store(root, store, storage_roots):
    """Keep the inputs whose image layout and image semantics match the store."""
    image_paths = {node.node_path for node in store.image_nodes}
    candidates = tuple(item for item in store.candidates
                       if set(_sources_by_node_path(item)) == image_paths)
    if not candidates:
        raise _KeepFull(REASON_IMAGE_LAYOUT_MISMATCH, label_node_paths=store.label_paths)
    try:
        guards = {node.node_path: read_zarr_semantic_guard(root, node.node_path)
                  for node in store.image_nodes}
    except PixelIdentityError as exc:
        raise _KeepFull(f"{REASON_UNSUPPORTED_NGFF}: {exc}",
                        label_node_paths=store.label_paths) from exc
    candidates = _candidates_matching_image_semantics(candidates, guards, storage_roots)
    if not candidates:
        raise _KeepFull(REASON_IMAGE_SEMANTICS_MISMATCH, label_node_paths=store.label_paths)
    return candidates


def _match_inputs(root, canonical_inputs, store, candidates, marked_input, bound_by_name,
                  provider, identity_workers, storage_roots):
    """Find the one input the store matches, by declaration or by pixel comparison.

    Raises `_KeepFull` when pixels changed, several inputs match or identities
    are unavailable.
    """
    basis, selected = _select_decision_basis(
        root, canonical_inputs, candidates, store.label_paths, marked_input)
    refreshed = {item.ordinal: item for item in candidates}
    if selected is not None:
        return _InputMatch(basis=basis, matched_input=selected, reason=basis,
                           image_identities=(),
                           refreshed_inputs=_changed_inputs(refreshed, canonical_inputs))
    returned_by_candidate = {item.ordinal: {} for item in candidates}
    try:
        matching_inputs = _inputs_matching_all_images(
            root, store.image_nodes, candidates, refreshed, returned_by_candidate,
            identity_workers=identity_workers, provider=provider,
            storage_roots=storage_roots)
    except (PixelIdentityError, OSError, ValueError) as exc:
        raise _KeepFull(f"{REASON_IDENTITY_UNAVAILABLE}: {exc}",
                        label_node_paths=store.label_paths,
                        refreshed_inputs=_changed_inputs(refreshed, canonical_inputs)) from exc
    refreshed_inputs = _changed_inputs(refreshed, canonical_inputs)
    if len(matching_inputs) != 1:
        raise _KeepFull(_unmatched_reason(matching_inputs, bound_by_name),
                        label_node_paths=store.label_paths,
                        refreshed_inputs=refreshed_inputs)
    matched_input = matching_inputs[0]
    image_identities = tuple(returned_by_candidate[matched_input.ordinal][node.node_path]
                             for node in store.image_nodes)
    reason = (REASON_INPUT_IMAGE_UNCHANGED if len(store.image_nodes) == 1
              else REASON_INPUT_PLATE_UNCHANGED)
    return _InputMatch(basis=basis, matched_input=matched_input, reason=reason,
                       image_identities=image_identities, refreshed_inputs=refreshed_inputs)


def _unmatched_reason(matching_inputs, bound_by_name):
    """Why a pixel comparison did not end in exactly one matching input."""
    if len(matching_inputs) > 1:
        return REASON_AMBIGUOUS_INPUT_IDENTITY
    return REASON_PIXELS_CHANGED if bound_by_name else REASON_NO_INPUT_IDENTITY_MATCH


def _eligible_decision(root, store, match, provider, identity_workers):
    """Identify the labels and build the eligible decision for a matched input."""
    keep_full_fields = dict(image_identities=match.image_identities,
                            label_node_paths=store.label_paths,
                            refreshed_inputs=match.refreshed_inputs)
    if any(source.interchange_profile != store.profile
           for source in _sources_by_node_path(match.matched_input).values()):
        raise _KeepFull(REASON_UNSUPPORTED_CANONICAL_PROFILE, **keep_full_fields)
    try:
        label_identities = _identities_for_nodes(
            root, store.label_nodes, provider, max_workers=identity_workers)
    except PixelIdentityError as exc:
        raise _KeepFull(f"{REASON_LABEL_IDENTITY_UNAVAILABLE}: {exc}",
                        **keep_full_fields) from exc
    return ReturnedZarrDecision(
        store_path=root,
        outcome=OUTCOME_ELIGIBLE,
        reason=match.reason,
        image_identities=match.image_identities,
        label_identities=label_identities,
        label_components=_label_components_of_match(match.matched_input, label_identities),
        label_node_paths=store.label_paths,
        matched_inputs=(match.matched_input,),
        decision_basis=match.basis,
        refreshed_inputs=match.refreshed_inputs,
    )



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
        return BASIS_RO_CRATE_OUTPUT, declared_input
    if canonical_inputs.original_pixels_not_required:
        # An explicit run/input filename binding is necessary for multiple inputs.
        choices = tuple(item for item in candidates
                        if same_input_artifact(item.transfer_artifact, root.name))
        if marked_input is not None:
            choices = candidates
        if len(choices) == 1:
            return BASIS_WORKFLOW_SETTING, choices[0]
    return BASIS_PIXEL_COMPARISON, None


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


def remove_transfer_input_marker(zarr_root: str | Path) -> bool:
    """Remove the temporary input binding before managed result storage."""
    marker = Path(zarr_root) / TRANSFER_INPUT_MARKER
    try:
        marker.unlink()
    except FileNotFoundError:
        return False
    return True
