"""Version-dispatched normalization and cheap receipt validation."""

from contextlib import contextmanager
from dataclasses import replace
from functools import partial
import json
import logging
import os
from pathlib import Path
import time

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT, ShallowOperationReport
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST, ShallowManifest

from . import __version__
from .adapters import ADAPTERS, FORMAT_ADAPTERS
from .ngff import PixelIdentityError, group_metadata, read_attrs
from .evaluation import (
    BASIS_PIXEL_COMPARISON, BASIS_WORKFLOW_SETTING, OUTCOME_KEEP_FULL,
    REASON_NORMALIZATION_FAILED, REASON_UNSUPPORTED_CANONICAL_PROFILE,
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
def locked(root):
    """Hold an exclusive lock on the store for the duration of the block.

    The lock file sits beside the store. Acquisition is non-blocking: a second
    process fails at once instead of waiting. Kernel locks (``flock`` on POSIX:
    an advisory whole-file lock; ``msvcrt.locking`` on Windows) release
    automatically after process death, unlike mkdir locks. Writable result
    storage must therefore support POSIX locks (see docs/index.md).
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


def validate_report(root, manifest, *, image=None, tool_version=None, artifact=None):
    """Check a committed operation report against its run and the stored result.

    Verifies the canonical snapshot, artifact, helper image and version, and,
    for a normalized result, that the stored shallow manifest, image metadata
    and label inventory match the report. Returns the report; raises ValueError
    on any mismatch. Cheap: no pixels are read.
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


def _check_report_provenance(report, manifest, root, image, tool_version, artifact):
    """Check the report belongs to this snapshot, artifact, helper image and version."""
    if report.canonical_inputs != manifest or report.artifact != (artifact or root.name):
        raise ValueError("Report canonical snapshot or artifact mismatch")
    if image is not None and report.image != image:
        raise ValueError("Unexpected helper image")
    if tool_version is not None and report.tool_version != tool_version:
        raise ValueError("Unexpected helper version")


def _find_authoritative_input(manifest, shallow_manifest):
    """Return the one canonical input whose sources the shallow manifest binds to."""
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


def _labels_of_input(canonical_input):
    """All labels the canonical input records, across a Plate's images."""
    if canonical_input.plate_source:
        return tuple(label for image in canonical_input.plate_source.images
                     for label in image.labels)
    return canonical_input.labels


def _check_image_binding(image_binding, manifest):
    """Check the evidence that justified replacing this image's pixels still holds."""
    if (image_binding.decision_basis == BASIS_PIXEL_COMPARISON
            and not pixel_identities_match(image_binding.returned_pixel_identity,
                                           image_binding.source.pixel_identity)):
        raise ValueError("Returned and canonical image identities differ")
    if (image_binding.decision_basis == BASIS_WORKFLOW_SETTING
            and not manifest.original_pixels_not_required):
        raise ValueError("Workflow shortcut was not selected for this run")


def _check_shallow_image_structure(root, image_node):
    """Check the image node points at the shallow manifest and lost its multiscales."""
    attrs = read_attrs(root / image_node.node_path)
    if ("multiscales" in attrs
            or attrs.get("biomero", {}).get("manifest") != SHALLOW_COLLECTION_MANIFEST):
        raise ValueError("Invalid shallow image structure")


def _check_label_inventory(shallow_manifest, image_node, label_bindings):
    """Check the image's declared labels are exactly the labels it has bindings for."""
    declared_paths = {
        label.node_path for label in shallow_manifest.collection.labels
        if label.source_image_id == image_node.node_id
    }
    bound_paths = {binding.component.logical_node_path for binding in label_bindings}
    if declared_paths != bound_paths:
        raise ValueError("Incomplete label inventory")


def _check_label_bindings(root, label_bindings, inherited_labels):
    """Check each label: inherited ones are absent and match the input; the rest are kept."""
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


def _same_source_binding(left, right):
    """Whether two canonical sources name the same stored object and generation."""
    fields = ("storage_root", "relative_path", "node_path", "source_object_type",
              "source_object_id", "source_generation", "interchange_profile")
    return all(getattr(left, field) == getattr(right, field) for field in fields)


def normalize(root, manifest, *, contract=DEFAULT_CONTRACT, identity_workers=1,
              failure_policy=KEEP_FULL_POLICY, image=None, task_id=None,
              identity_provider=None, measure_bytes=False,
              storage_roots=None):
    """Shallow one returned store, or keep it full, and return the operation report.

    Recovers any interrupted prune first and reuses an already committed
    report, so rerunning after an interruption is safe.
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


def _reject_symlinks(root):
    """Never traverse a link into a canonical store, even through metadata.

    ``root`` itself was checked before the lock was taken.
    """
    if root.is_dir():
        for directory, dirs, names in os.walk(root):
            if any((Path(directory) / name).is_symlink() for name in dirs + names):
                raise ValueError("Returned Zarr contains a symlink")


def _adapter_for_store(root, default):
    """Pick the adapter for the store's Zarr format, or the contract's default."""
    try:
        return FORMAT_ADAPTERS[group_metadata(root)[3]]
    except PixelIdentityError:
        # Discovery records the unsupported metadata as a keep-full result.
        return default


def _evaluate_store(adapter, root, manifest, identity_workers, identity_provider,
                    storage_roots):
    """Evaluate the store; an eligible match whose canonical profile differs is kept full."""
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


def _manifest_with_refreshed_inputs(manifest, refreshed_inputs):
    """Return the manifest with each input replaced by its refreshed copy, if any."""
    # Reversed so the first refreshed copy of an ordinal wins.
    refreshed_by_ordinal = {item.ordinal: item for item in reversed(refreshed_inputs)}
    return manifest.model_copy(update={"inputs": tuple(
        refreshed_by_ordinal.get(item.ordinal, item) for item in manifest.inputs)})


def _operation_report(root, manifest, decision, adapter, image, task_id,
                      started, evaluated, shallow_manifest=None, reason=None):
    """Build the operation report; ``shallow_manifest`` marks a normalized result."""
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


def _normalize_eligible(root, manifest, decision, adapter, report_for, *,
                        image, measure_bytes):
    """Prune an eligible store; fall back to keep-full if that fails and recovers.

    The trusted report is written and validated just before the prune commits.
    """
    committed_reports = []

    def commit(shallow_manifest):
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
