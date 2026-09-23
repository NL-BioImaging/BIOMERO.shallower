"""Version-dispatched normalization and cheap receipt validation."""

from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import time

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT, ShallowOperationReport
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST, ShallowManifest

from . import __version__
from .adapters import ADAPTERS
from .pixel_identity import pixel_identities_match
from .transaction import recover, write_json


@contextmanager
def locked(root):
    # Kernel locks release automatically after process death, unlike mkdir locks.
    path = root.with_name(f".{root.name}.biomero-lock")
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
    root = Path(root)
    report = ShallowOperationReport.from_dict(json.loads(
        (root / SHALLOW_OPERATION_REPORT).read_text(encoding="utf-8")))
    if report.canonical_inputs != manifest or report.artifact != (artifact or root.name):
        raise ValueError("Report canonical snapshot or artifact mismatch")
    if image is not None and report.image != image:
        raise ValueError("Unexpected helper image")
    if tool_version is not None and report.tool_version != tool_version:
        raise ValueError("Unexpected helper version")
    if report.manifest is None:
        return report
    shallow_manifest = ShallowManifest.from_dict(json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")))
    if shallow_manifest != report.manifest:
        raise ValueError("Manifest does not match verified report")
    matches = []
    for item in manifest.inputs:
        sources = ([image.source for image in item.plate_source.images]
                   if item.plate_source else [item.source])
        if {source.node_path for source in sources} == {
                image.node_path
                for image in shallow_manifest.collection.images
        } and all(
                binding.source in sources
                for binding in shallow_manifest.bindings.images):
            matches.append(item)
    if len(matches) != 1:
        raise ValueError("No unique authoritative canonical source")
    canonical = matches[0]
    labels = (tuple(label for image in canonical.plate_source.images
                    for label in image.labels) if canonical.plate_source
              else canonical.labels)
    for image_node in shallow_manifest.collection.images:
        image_binding = shallow_manifest.image_binding(image_node.node_id)
        if not image_binding.source.canonical_pixel_verified:
            raise ValueError("Canonical image has not been verified")
        if not pixel_identities_match(
                image_binding.returned_pixel_identity,
                image_binding.source.pixel_identity):
            raise ValueError("Returned and canonical image identities differ")
        attrs = json.loads(
            (root / image_node.node_path / ".zattrs").read_text()
        )
        if ("multiscales" in attrs
                or attrs.get("biomero", {}).get("manifest")
                != SHALLOW_COLLECTION_MANIFEST):
            raise ValueError("Invalid shallow image structure")
        label_nodes = tuple(
            label for label in shallow_manifest.collection.labels
            if label.source_image_id == image_node.node_id
        )
        label_bindings = shallow_manifest.label_bindings_for_image(
            image_node.node_id
        )
        if {label.node_path for label in label_nodes} != {
                binding.component.logical_node_path
                for binding in label_bindings}:
            raise ValueError("Incomplete label inventory")
        for binding in label_bindings:
            component = binding.component
            path = root / component.logical_node_path
            if component.source is not None:
                if not any(label.source == component.source
                           and label.logical_node_path == component.logical_node_path
                           and pixel_identities_match(label.pixel_identity, component.pixel_identity)
                           for label in labels) or path.exists():
                    raise ValueError("Invalid inherited label reference")
            elif not path.is_dir():
                raise ValueError("Missing retained label")
    return report


def normalize(root, manifest, *, contract=1, identity_workers=1,
              failure_policy="keep-full", image=None, task_id=None,
              identity_provider=None, measure_bytes=False):
    if contract not in ADAPTERS:
        raise ValueError(f"Unknown contract version: {contract}")
    adapter = ADAPTERS[contract]
    if failure_policy != "keep-full":
        raise ValueError("Unknown failure policy")
    root = Path(root).absolute()
    if root.is_symlink():
        raise ValueError("Returned Zarr cannot be a symlink")
    with locked(root):
        recover(root)
        if (root / SHALLOW_OPERATION_REPORT).exists():
            return validate_report(root, manifest, image=image, tool_version=__version__)
        if (root / SHALLOW_COLLECTION_MANIFEST).exists():
            raise ValueError("Shallow result has no committed trusted report")
        # Never traverse a link into a canonical store, even through metadata.
        if root.is_symlink():
            raise ValueError("Returned Zarr cannot be a symlink")
        if root.is_dir():
            for directory, dirs, names in os.walk(root):
                if any((Path(directory) / name).is_symlink() for name in dirs + names):
                    raise ValueError("Returned Zarr contains a symlink")
        started = time.monotonic()
        decision = adapter.evaluate(root, manifest,
                                    identity_workers=identity_workers,
                                    identity_provider=identity_provider)
        if decision.eligible:
            source = decision.matched_inputs[0]
            profile = (source.plate_source.interchange_profile if source.plate_source
                       else source.source.interchange_profile)
            if profile != adapter.profile:
                decision = replace(decision, outcome="keep-full",
                                   reason="unsupported-canonical-profile")
        evaluated = time.monotonic()

        def report_for(shallow_manifest=None, reason=None):
            return ShallowOperationReport(
                schema=2, toolVersion=__version__, image=image,
                inputContract=1, outputContract=2, adapter=adapter.profile,
                canonicalInputs=manifest, artifact=root.name,
                decision=decision.outcome, reason=reason or decision.reason,
                result="normalized" if shallow_manifest else "kept-full",
                manifest=shallow_manifest,
                timings={"identity": evaluated - started,
                         "normalization": time.monotonic() - evaluated},
                slurmJobId=os.getenv("SLURM_JOB_ID"), taskId=task_id,
            )

        report = report_for()
        if decision.eligible:
            def commit(shallow_manifest):
                nonlocal report
                report = report_for(shallow_manifest)
                write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
                validate_report(root, manifest, image=image, tool_version=__version__)
            try:
                normalized = adapter.normalize(
                    decision, manifest.workflow_id,
                    before_commit=commit, measure_bytes=measure_bytes)
                if measure_bytes:
                    report = report.model_copy(update={
                        "bytes_before": normalized.bytes_before,
                        "bytes_after": normalized.bytes_after,
                    })
                    write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
            except Exception as exc:
                # Recovery must succeed before a full-result fallback is safe.
                recover(root)
                if (root / SHALLOW_OPERATION_REPORT).exists():
                    return validate_report(root, manifest, image=image, tool_version=__version__)
                report = report_for(reason=f"normalization-failed: {exc}")
        if root.is_dir() and report.manifest is None:
            write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
        return report
