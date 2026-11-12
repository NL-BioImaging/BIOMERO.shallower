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
"""Version-dispatched normalization and cheap receipt validation."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT, ShallowOperationReport
from biomero_schema.zarr import (
    SHALLOW_COLLECTION_MANIFEST, CanonicalInput, CanonicalInputManifest, ShallowManifest,
)

from . import __version__
from .adapters import ADAPTERS, FORMAT_ADAPTERS
from .ngff import PixelIdentityError, group_metadata, read_attrs
from .evaluation import (
    BASIS_PIXEL_COMPARISON, BASIS_WORKFLOW_SETTING, OUTCOME_KEEP_FULL,
    REASON_NORMALIZATION_FAILED, REASON_UNSUPPORTED_CANONICAL_PROFILE, ReturnedZarrDecision,
)
from .filesystem import write_json
from .pixel_identity import pixel_identities_match
from .transaction import recover

__all__ = [
    "ADAPTERS",
    "DEFAULT_CONTRACT",
    "KEEP_FULL_POLICY",
    "RESULT_KEPT_FULL",
    "RESULT_NORMALIZED",
    "locked",
    "normalize",
    "validate_report",
]

logger = logging.getLogger(__name__)

DEFAULT_CONTRACT = 1
KEEP_FULL_POLICY = "keep-full"
# Values of the operation report's ``result``.
RESULT_NORMALIZED = "normalized"
RESULT_KEPT_FULL = "kept-full"
# Versions recorded in every operation report.
REPORT_SCHEMA = 2
INPUT_CONTRACT = 1
OUTPUT_CONTRACT = 2
LOCK_INFIX = ".biomero-lock"


@contextmanager
def locked(root: Path) -> Iterator[None]:
    """
    Hold an exclusive lock on the store for the duration of the block.

    The lock file sits beside the store. Acquisition is non-blocking: a second
    process fails at once instead of waiting. Kernel locks (``flock`` on POSIX:
    an advisory whole-file lock; ``msvcrt.locking`` on Windows) release
    automatically after process death, unlike mkdir locks. Writable result
    storage must therefore support POSIX locks (see docs/index.md).

    Args:
        root (Path): The store directory to lock.

    Yields:
        None: While the lock is held.

    Raises:
        OSError: If the lock is already held by another process.
    """
    path = root.with_name(f".{root.name}{LOCK_INFIX}")
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def validate_report(
    root: str | Path,
    manifest: CanonicalInputManifest,
    *,
    image: str | None = None,
    tool_version: str | None = None,
    artifact: str | None = None,
) -> ShallowOperationReport:
    """
    Check a committed operation report against its run and the stored result.

    Verifies the canonical snapshot, artifact, helper image and version, and,
    for a normalized result, that the stored shallow manifest, image metadata
    and label inventory match the report. Cheap: no pixels are read.

    Args:
        root (str | Path): The returned store holding the committed report.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        image (str, optional): The expected helper image; not checked when None.
        tool_version (str, optional): The expected helper version; not checked
            when None.
        artifact (str, optional): The expected artifact name; defaults to the
            store directory name.

    Returns:
        ShallowOperationReport:
            The validated report.

    Raises:
        ValueError: On any mismatch between the report, the run and the store.
    """
    root = Path(root)
    report = ShallowOperationReport.from_dict(json.loads(
        (root / SHALLOW_OPERATION_REPORT).read_text(encoding="utf-8")))
    _check_report_provenance(report, manifest, root, image, tool_version, artifact)
    if report.manifest is None:
        return report
    shallow_manifest = ShallowManifest.from_dict(json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")))
    if shallow_manifest != report.manifest:
        raise ValueError("Manifest does not match verified report")
    if report.adapter != shallow_manifest.interchange_profile:
        raise ValueError("Report adapter and manifest profile differ")
    authoritative_input = _find_authoritative_input(manifest, shallow_manifest)
    inherited_labels = _labels_of_input(authoritative_input)
    for image_node in shallow_manifest.collection.images:
        _check_image_binding(shallow_manifest.image_binding(image_node.node_id), manifest)
        _check_shallow_image_structure(root, image_node)
        label_bindings = shallow_manifest.label_bindings_for_image(image_node.node_id)
        _check_label_inventory(shallow_manifest, image_node, label_bindings)
        _check_label_bindings(root, label_bindings, inherited_labels)
    return report


def _check_report_provenance(
    report: ShallowOperationReport,
    manifest: CanonicalInputManifest,
    root: Path,
    image: str | None,
    tool_version: str | None,
    artifact: str | None,
) -> None:
    """
    Check the report belongs to this snapshot, artifact, helper image and version.

    Args:
        report (ShallowOperationReport): The committed report.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        root (Path): The store directory.
        image (str | None): The expected helper image, if any.
        tool_version (str | None): The expected helper version, if any.
        artifact (str | None): The expected artifact name, if any.

    Raises:
        ValueError: If the snapshot, artifact, helper image or version differ.
    """
    if report.canonical_inputs != manifest or report.artifact != (artifact or root.name):
        raise ValueError("Report canonical snapshot or artifact mismatch")
    if image is not None and report.image != image:
        raise ValueError("Unexpected helper image")
    if tool_version is not None and report.tool_version != tool_version:
        raise ValueError("Unexpected helper version")


def _find_authoritative_input(
    manifest: CanonicalInputManifest, shallow_manifest: ShallowManifest
) -> CanonicalInput:
    """
    Return the one canonical input whose sources the shallow manifest binds to.

    Args:
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        shallow_manifest (ShallowManifest): The stored shallow manifest.

    Returns:
        CanonicalInput:
            The unique matching canonical input.

    Raises:
        ValueError: If no single canonical input matches.
    """
    shallow_image_paths = {image.node_path for image in shallow_manifest.collection.images}
    candidates = []
    for item in manifest.inputs:
        sources = ([image.source for image in item.plate_source.images]
                   if item.plate_source else [item.source])
        covers_same_images = {source.node_path for source in sources} == shallow_image_paths
        binds_to_sources = all(
            any(_same_source_binding(binding.source, source) for source in sources)
            for binding in shallow_manifest.bindings.images)
        if covers_same_images and binds_to_sources:
            candidates.append(item)
    if len(candidates) != 1:
        raise ValueError("No unique authoritative canonical source")
    return candidates[0]


def _labels_of_input(canonical_input: CanonicalInput) -> tuple[Any, ...]:
    """
    All labels the canonical input records, across a Plate's images.

    Args:
        canonical_input (CanonicalInput): The canonical input to read labels from.

    Returns:
        tuple[Any, ...]:
            The recorded label components.
    """
    if canonical_input.plate_source:
        return tuple(label for image in canonical_input.plate_source.images
                     for label in image.labels)
    return canonical_input.labels


def _check_image_binding(image_binding: Any, manifest: CanonicalInputManifest) -> None:
    """
    Check the evidence that justified replacing this image's pixels still holds.

    Args:
        image_binding (Any): The shallow manifest's binding for one image.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.

    Raises:
        ValueError: If the pixel identities differ or the workflow shortcut was
            not selected for this run.
    """
    if (image_binding.decision_basis == BASIS_PIXEL_COMPARISON
            and not pixel_identities_match(image_binding.returned_pixel_identity,
                                           image_binding.source.pixel_identity)):
        raise ValueError("Returned and canonical image identities differ")
    if (image_binding.decision_basis == BASIS_WORKFLOW_SETTING
            and not manifest.original_pixels_not_required):
        raise ValueError("Workflow shortcut was not selected for this run")


def _check_shallow_image_structure(root: Path, image_node: Any) -> None:
    """
    Check the image node points at the shallow manifest and lost its multiscales.

    Args:
        root (Path): The store directory.
        image_node (Any): The shallow manifest's node for one image.

    Raises:
        ValueError: If the image node still has multiscales or does not link
            to the shallow manifest.
    """
    attrs = read_attrs(root / image_node.node_path)
    if ("multiscales" in attrs
            or attrs.get("biomero", {}).get("manifest") != SHALLOW_COLLECTION_MANIFEST):
        raise ValueError("Invalid shallow image structure")


def _check_label_inventory(
    shallow_manifest: ShallowManifest, image_node: Any, label_bindings: Any
) -> None:
    """
    Check the image's declared labels are exactly the labels it has bindings for.

    Args:
        shallow_manifest (ShallowManifest): The stored shallow manifest.
        image_node (Any): The shallow manifest's node for one image.
        label_bindings (Any): The label bindings of that image.

    Raises:
        ValueError: If the declared and bound label paths differ.
    """
    declared_paths = {
        label.node_path for label in shallow_manifest.collection.labels
        if label.source_image_id == image_node.node_id
    }
    bound_paths = {binding.component.logical_node_path for binding in label_bindings}
    if declared_paths != bound_paths:
        raise ValueError("Incomplete label inventory")


def _check_label_bindings(
    root: Path, label_bindings: Any, inherited_labels: tuple[Any, ...]
) -> None:
    """
    Check each label: inherited ones are absent and match the input; the rest are kept.

    Args:
        root (Path): The store directory.
        label_bindings (Any): The label bindings of one image.
        inherited_labels (tuple[Any, ...]): The labels recorded by the canonical input.

    Raises:
        ValueError: If an inherited label reference is invalid or a retained
            label is missing.
    """
    for binding in label_bindings:
        component = binding.component
        path = root / component.logical_node_path
        if component.source is not None:
            matches_input_label = any(
                label.source == component.source
                and label.logical_node_path == component.logical_node_path
                and pixel_identities_match(label.pixel_identity, component.pixel_identity)
                for label in inherited_labels)
            if not matches_input_label or path.exists():
                raise ValueError("Invalid inherited label reference")
        elif not path.is_dir():
            raise ValueError("Missing retained label")


def _same_source_binding(left: Any, right: Any) -> bool:
    """
    Whether two canonical sources name the same stored object and generation.

    Args:
        left (Any): The first canonical source.
        right (Any): The second canonical source.

    Returns:
        bool:
            True if all identifying fields are equal.
    """
    fields = ("storage_root", "relative_path", "node_path", "source_object_type",
              "source_object_id", "source_generation", "interchange_profile")
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def normalize(
    root: str | Path,
    manifest: CanonicalInputManifest,
    *,
    contract: int = DEFAULT_CONTRACT,
    identity_workers: int = 1,
    failure_policy: str = KEEP_FULL_POLICY,
    image: str | None = None,
    task_id: str | None = None,
    identity_provider: Any = None,
    measure_bytes: bool = False,
    storage_roots: Any = None,
) -> ShallowOperationReport:
    """
    Shallow one returned store, or keep it full, and return the operation report.

    Recovers any interrupted prune first and reuses an already committed
    report, so rerunning after an interruption is safe.

    Args:
        root (str | Path): The returned store to normalize.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        contract (int, optional): The contract version selecting the adapter.
        identity_workers (int, optional): Number of pixel identity workers.
        failure_policy (str, optional): What to do on failure; only keep-full
            is supported.
        image (str, optional): The helper image recorded in the report.
        task_id (str, optional): The task identifier recorded in the report.
        identity_provider (Any, optional): Pixel identity provider override.
        measure_bytes (bool, optional): Whether to record the bytes before and
            after normalization.
        storage_roots (Any, optional): Storage roots used to resolve canonical
            sources.

    Returns:
        ShallowOperationReport:
            The operation report of the (possibly reused) result.

    Raises:
        ValueError: If the contract version or failure policy is unknown, the
            store is or contains a symlink, or a shallow result has no
            committed trusted report.
    """
    if contract not in ADAPTERS:
        raise ValueError(f"Unknown contract version: {contract}")
    adapter = ADAPTERS[contract]
    if failure_policy != KEEP_FULL_POLICY:
        raise ValueError("Unknown failure policy")
    root = Path(root).absolute()
    logger.debug(f"Normalizing {root}: contract={contract}, workers={identity_workers}, "
                 f"image={image}, task={task_id}")
    if root.is_symlink():
        raise ValueError("Returned Zarr cannot be a symlink")
    with locked(root):
        recover(root)
        if (root / SHALLOW_OPERATION_REPORT).exists():
            logger.info(f"Reusing committed report of {root}")
            return validate_report(root, manifest, image=image, tool_version=__version__)
        if (root / SHALLOW_COLLECTION_MANIFEST).exists():
            raise ValueError("Shallow result has no committed trusted report")
        _reject_symlinks(root)
        adapter = _adapter_for_store(root, default=adapter)
        started = time.monotonic()
        decision = _evaluate_store(adapter, root, manifest, identity_workers,
                                   identity_provider, storage_roots)
        evaluated = time.monotonic()
        report_for = partial(_operation_report, root, manifest, decision, adapter,
                             image, task_id, started, evaluated)

        if decision.eligible:
            report = _normalize_eligible(root, manifest, decision, adapter, report_for,
                                         image=image, measure_bytes=measure_bytes)
        else:
            report = report_for()
        store_still_exists = root.is_dir()
        if store_still_exists and report.manifest is None:
            write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
        logger.info(f"Normalized {root}: result={report.result}, reason={report.reason}")
        return report


def _reject_symlinks(root: Path) -> None:
    """
    Never traverse a link into a canonical store, even through metadata.

    ``root`` itself was checked before the lock was taken.

    Args:
        root (Path): The store directory to walk.

    Raises:
        ValueError: If the store contains a symlink.
    """
    if root.is_dir():
        for directory, dirs, names in os.walk(root):
            if any((Path(directory) / name).is_symlink() for name in dirs + names):
                raise ValueError("Returned Zarr contains a symlink")


def _adapter_for_store(root: Path, default: Any) -> Any:
    """
    Pick the adapter for the store's Zarr format, or the contract's default.

    Args:
        root (Path): The store directory.
        default (Any): The adapter of the requested contract.

    Returns:
        Any:
            The adapter for the store's Zarr format, or ``default`` when the
            metadata is unsupported.
    """
    try:
        return FORMAT_ADAPTERS[group_metadata(root)[3]]
    except PixelIdentityError:
        # Discovery records the unsupported metadata as a keep-full result.
        return default


def _evaluate_store(
    adapter: Any,
    root: Path,
    manifest: CanonicalInputManifest,
    identity_workers: int,
    identity_provider: Any,
    storage_roots: Any,
) -> ReturnedZarrDecision:
    """
    Evaluate the store; an eligible match whose canonical profile differs is kept full.

    Args:
        adapter (Any): The adapter evaluating the store.
        root (Path): The store directory.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        identity_workers (int): Number of pixel identity workers.
        identity_provider (Any): Pixel identity provider override.
        storage_roots (Any): Storage roots used to resolve canonical sources.

    Returns:
        ReturnedZarrDecision:
            The decision for the store.
    """
    decision = adapter.evaluate(root, manifest,
                                identity_workers=identity_workers,
                                identity_provider=identity_provider,
                                storage_roots=storage_roots)
    if not decision.eligible:
        return decision
    matched_input = decision.matched_inputs[0]
    canonical_profile = (matched_input.plate_source.interchange_profile
                         if matched_input.plate_source
                         else matched_input.source.interchange_profile)
    if canonical_profile != adapter.profile:
        logger.warning(f"Keeping {root} full: canonical profile {canonical_profile} "
                       f"differs from {adapter.profile}")
        return replace(decision, outcome=OUTCOME_KEEP_FULL,
                       reason=REASON_UNSUPPORTED_CANONICAL_PROFILE)
    return decision


def _manifest_with_refreshed_inputs(
    manifest: CanonicalInputManifest, refreshed_inputs: tuple[CanonicalInput, ...]
) -> CanonicalInputManifest:
    """
    Return the manifest with each input replaced by its refreshed copy, if any.

    Args:
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        refreshed_inputs (tuple[CanonicalInput, ...]): The refreshed inputs.

    Returns:
        CanonicalInputManifest:
            A copy of the manifest with the refreshed inputs substituted.
    """
    # Reversed so the first refreshed copy of an ordinal wins.
    refreshed_by_ordinal = {item.ordinal: item for item in reversed(refreshed_inputs)}
    return manifest.model_copy(update={"inputs": tuple(
        refreshed_by_ordinal.get(item.ordinal, item) for item in manifest.inputs)})


def _operation_report(
    root: Path,
    manifest: CanonicalInputManifest,
    decision: ReturnedZarrDecision,
    adapter: Any,
    image: str | None,
    task_id: str | None,
    started: float,
    evaluated: float,
    shallow_manifest: ShallowManifest | None = None,
    reason: str | None = None,
) -> ShallowOperationReport:
    """
    Build the operation report; ``shallow_manifest`` marks a normalized result.

    Args:
        root (Path): The store directory.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        decision (ReturnedZarrDecision): The decision for the store.
        adapter (Any): The adapter that handled the store.
        image (str | None): The helper image to record.
        task_id (str | None): The task identifier to record.
        started (float): Monotonic time when evaluation started.
        evaluated (float): Monotonic time when evaluation finished.
        shallow_manifest (ShallowManifest, optional): The shallow manifest of a
            normalized result.
        reason (str, optional): Overrides the decision's reason.

    Returns:
        ShallowOperationReport:
            The assembled report.
    """
    return ShallowOperationReport(
        schema=REPORT_SCHEMA, toolVersion=__version__, image=image,
        inputContract=INPUT_CONTRACT, outputContract=OUTPUT_CONTRACT,
        adapter=adapter.profile,
        canonicalInputs=manifest, artifact=root.name,
        refreshedInputs=(_manifest_with_refreshed_inputs(manifest, decision.refreshed_inputs)
                         if decision.refreshed_inputs else None),
        decision=decision.outcome, reason=reason or decision.reason,
        result=RESULT_NORMALIZED if shallow_manifest else RESULT_KEPT_FULL,
        manifest=shallow_manifest,
        timings={"identity": evaluated - started,
                 "normalization": time.monotonic() - evaluated},
        slurmJobId=os.getenv("SLURM_JOB_ID"), taskId=task_id,
    )


def _normalize_eligible(
    root: Path,
    manifest: CanonicalInputManifest,
    decision: ReturnedZarrDecision,
    adapter: Any,
    report_for: Callable[..., ShallowOperationReport],
    *,
    image: str | None,
    measure_bytes: bool,
) -> ShallowOperationReport:
    """
    Prune an eligible store; fall back to keep-full if that fails and recovers.

    The trusted report is written and validated just before the prune commits.

    Args:
        root (Path): The store directory.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        decision (ReturnedZarrDecision): The eligible decision for the store.
        adapter (Any): The adapter that prunes the store.
        report_for (Callable[..., ShallowOperationReport]): Builds the operation
            report, optionally for a shallow manifest or with a reason.
        image (str | None): The expected helper image.
        measure_bytes (bool): Whether to record the bytes before and after.

    Returns:
        ShallowOperationReport:
            The report of the normalized or kept-full result.
    """
    committed_reports = []

    def commit(shallow_manifest: ShallowManifest) -> None:
        """Write and validate the trusted report just before the prune commits."""
        committed_reports.append(report_for(shallow_manifest))
        write_json(root / SHALLOW_OPERATION_REPORT, committed_reports[-1].to_dict())
        validate_report(root, manifest, image=image, tool_version=__version__)

    try:
        normalized = adapter.normalize(
            decision, manifest.workflow_id,
            before_commit=commit, measure_bytes=measure_bytes)
        report = committed_reports[-1]
        if measure_bytes:
            report = report.model_copy(update={
                "bytes_before": normalized.bytes_before,
                "bytes_after": normalized.bytes_after,
            })
            write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
    except Exception as exc:
        # Recovery must succeed before a full-result fallback is safe.
        logger.warning(f"Normalization of {root} failed: {exc}", exc_info=True)
        recover(root)
        if (root / SHALLOW_OPERATION_REPORT).exists():
            logger.info(f"Normalization of {root} had committed before the failure")
            return validate_report(root, manifest, image=image, tool_version=__version__)
        logger.warning(f"Keeping {root} full: {REASON_NORMALIZATION_FAILED}")
        report = report_for(reason=f"{REASON_NORMALIZATION_FAILED}: {exc}")
    return report
