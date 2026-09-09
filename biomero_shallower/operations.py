"""Version-dispatched normalization and cheap receipt validation."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

from biomero_schema.shallower import SHALLOW_OPERATION_REPORT, ShallowOperationReport
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST, ShallowCollection

from . import __version__
from .pixel_identity import pixel_identities_match
from .result_zarr import evaluate_returned_zarr, normalize_returned_zarr
from .transaction import recover, write_json

ADAPTERS = {1: "ngff-0.4-zarr-v2"}


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


def validate_report(root, manifest, *, image=None, tool_version=None):
    root = Path(root)
    report = ShallowOperationReport.from_dict(json.loads(
        (root / SHALLOW_OPERATION_REPORT).read_text(encoding="utf-8")))
    if report.canonical_inputs != manifest or report.artifact != root.name:
        raise ValueError("Report canonical snapshot or artifact mismatch")
    if image is not None and report.image != image:
        raise ValueError("Unexpected helper image")
    if tool_version is not None and report.tool_version != tool_version:
        raise ValueError("Unexpected helper version")
    if report.collection is None:
        return report
    collection = ShallowCollection.from_dict(json.loads(
        (root / SHALLOW_COLLECTION_MANIFEST).read_text(encoding="utf-8")))
    if collection != report.collection:
        raise ValueError("Manifest does not match verified report")
    matches = []
    for item in manifest.inputs:
        sources = ([image.source for image in item.plate_source.images]
                   if item.plate_source else [item.source])
        if {source.node_path for source in sources} == {
                image.image_node_path for image in collection.images} and all(
                image.source in sources for image in collection.images):
            matches.append(item)
    if len(matches) != 1:
        raise ValueError("No unique authoritative canonical source")
    canonical = matches[0]
    labels = (tuple(label for image in canonical.plate_source.images
                    for label in image.labels) if canonical.plate_source
              else canonical.labels)
    for image_ref in collection.images:
        if not pixel_identities_match(image_ref.returned_pixel_identity,
                                      image_ref.source.pixel_identity):
            raise ValueError("Returned and canonical image identities differ")
        attrs = json.loads((root / image_ref.image_node_path / ".zattrs").read_text())
        if "multiscales" in attrs or attrs.get("biomero", {}).get("manifest") != SHALLOW_COLLECTION_MANIFEST:
            raise ValueError("Invalid shallow image structure")
        if set(image_ref.label_node_paths) != {c.logical_node_path for c in image_ref.label_components}:
            raise ValueError("Incomplete label inventory")
        for component in image_ref.label_components:
            path = root / component.logical_node_path
            if component.source is not None:
                if component not in labels or path.exists():
                    raise ValueError("Invalid inherited label reference")
            elif not path.is_dir():
                raise ValueError("Missing retained label")
    return report


def normalize(root, manifest, *, contract=1, identity_workers=1,
              failure_policy="keep-full", image=None, task_id=None,
              identity_provider=None, measure_bytes=False):
    if contract not in ADAPTERS:
        raise ValueError(f"Unknown contract version: {contract}")
    if failure_policy != "keep-full":
        raise ValueError("Unknown failure policy")
    root = Path(root).absolute()
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
        decision = evaluate_returned_zarr(root, manifest,
                                         identity_workers=identity_workers,
                                         identity_provider=identity_provider)
        evaluated = time.monotonic()

        def report_for(collection=None, reason=None):
            return ShallowOperationReport(
                schema=1, toolVersion=__version__, image=image,
                inputContract=1, outputContract=1, adapter=ADAPTERS[contract],
                canonicalInputs=manifest, artifact=root.name,
                decision=decision.outcome, reason=reason or decision.reason,
                result="normalized" if collection else (
                    "skipped" if decision.unchanged_passthrough else "kept-full"),
                collection=collection,
                timings={"identity": evaluated - started,
                         "normalization": time.monotonic() - evaluated},
                slurmJobId=os.getenv("SLURM_JOB_ID"), taskId=task_id,
            )

        report = report_for()
        if decision.eligible:
            def commit(collection):
                nonlocal report
                report = report_for(collection)
                write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
                validate_report(root, manifest, image=image, tool_version=__version__)
            try:
                normalize_returned_zarr(decision, manifest.workflow_id,
                                        before_commit=commit, measure_bytes=measure_bytes)
            except Exception as exc:
                # Recovery must succeed before a full-result fallback is safe.
                recover(root)
                if (root / SHALLOW_OPERATION_REPORT).exists():
                    return validate_report(root, manifest, image=image, tool_version=__version__)
                report = report_for(reason=f"normalization-failed: {exc}")
        if root.is_dir() and report.collection is None:
            write_json(root / SHALLOW_OPERATION_REPORT, report.to_dict())
        return report
