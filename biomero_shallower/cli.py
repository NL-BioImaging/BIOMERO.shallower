"""Noninteractive filesystem CLI. Exit 0: safe terminal; 2: invalid/unsafe.

Every command prints one JSON document on stdout; errors print JSON on stderr.
Logs go to stderr too, so stdout stays machine-readable.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import sys

from biomero_schema.shallower import (
    SHALLOW_BATCH_REPORT, SHALLOW_OPERATION_REPORT,
    RemoteShallowReceipt, ShallowBatchReport,
)
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST, CanonicalInputManifest

from . import __version__
from .capabilities import capabilities
from .discovery import discover_ngff_nodes, find_returned_zarr_stores
from .migration import migrate_shallow_store_v1
from .operations import (
    ADAPTERS, KEEP_FULL_POLICY, RESULT_NORMALIZED, locked, normalize, validate_report,
)
from .pixel_identity import IsccBioIdentityProvider
from .transaction import recover, write_json

__all__ = ["configure_logging", "load_manifest", "main", "normalize_tree"]

logger = logging.getLogger(__name__)

LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
IMAGE_ENV = "BIOMERO_SHALLOWER_IMAGE"
EXIT_OK = 0
EXIT_ERROR = 2
# Schema numbers of the documents this CLI reads and writes.
CANONICAL_MANIFEST_SCHEMA = 1
INSPECT_SCHEMA = 1
RECEIPT_SCHEMA = 1
BATCH_SCHEMA = 1
BATCH_RESULT_COMPLETE = "complete"
RESULT_HEALTHY = "healthy"
RESULT_ERROR = "error"


def load_manifest(path):
    """Read a canonical input manifest, rejecting a missing or unknown schema."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != CANONICAL_MANIFEST_SCHEMA:
        raise ValueError("Missing or unknown canonical manifest contract version")
    return CanonicalInputManifest.from_dict(raw)


def _recover_store(store, manifest, image):
    """Finish an interrupted prune of ``store`` and return its validated report.

    Returns None for a store that was never shallowed; refuses one that holds a
    shallow manifest without a trusted report.
    """
    with locked(store):
        recover(store)
    if not (store / SHALLOW_OPERATION_REPORT).exists():
        if (store / SHALLOW_COLLECTION_MANIFEST).exists():
            raise ValueError("Untrusted shallow result cannot fall back")
        return None
    return validate_report(store, manifest, image=image, tool_version=__version__)


def _receipt_for(store, root, report, image):
    """Build the receipt that lets a caller trust the store's committed report."""
    return RemoteShallowReceipt(
        schema=RECEIPT_SCHEMA, image=image, toolVersion=__version__,
        reportSha256=hashlib.sha256(
            (store / SHALLOW_OPERATION_REPORT).read_bytes()).hexdigest(),
        artifactPath=store.relative_to(root).as_posix(),
        slurmJobId=report.slurm_job_id, taskId=report.task_id,
    )


def normalize_tree(root, manifest, *, image, task_id, workers=1,
                   recover_only=False):
    """Normalize (or only recover) every returned store under ``root``.

    Writes and returns a batch report with a receipt for each store that was
    normalized or whose inputs were refreshed.
    """
    root = Path(root)
    receipts = []
    stores = find_returned_zarr_stores(root)
    logger.info(f"Found {len(stores)} returned Zarr stores under {root} "
                f"(recover_only={recover_only})")
    for store in stores:
        if recover_only:
            report = _recover_store(store, manifest, image)
            if report is None:
                continue
        else:
            report = normalize(store, manifest, image=image, task_id=task_id,
                               identity_workers=workers)
        if report.result == RESULT_NORMALIZED or report.refreshed_inputs is not None:
            receipts.append(_receipt_for(store, root, report, image))
    batch = ShallowBatchReport(schema=BATCH_SCHEMA, canonicalInputs=manifest, image=image,
                               toolVersion=__version__, result=BATCH_RESULT_COMPLETE,
                               receipts=tuple(receipts))
    write_json(root / SHALLOW_BATCH_REPORT, batch.to_dict())
    return batch


def configure_logging(level=LOG_LEVEL, log_format=LOG_FORMAT):
    """Log to stderr at ``level`` unless the caller already configured logging.

    Library modules never configure logging. Standard output stays the single
    JSON document, so logs go to stderr (the Slurm job log for helper runs).
    """
    logging.basicConfig(level=level, format=log_format)


def _add_store_arguments(command, *, recover_only_option):
    """Add the arguments shared by the commands that work on a store and its inputs."""
    command.add_argument("--returned-zarr", required=True, type=Path)
    command.add_argument("--canonical-inputs", required=True, type=Path)
    command.add_argument("--contract-version", required=True, type=int, choices=ADAPTERS)
    command.add_argument("--report", required=True, type=Path)
    command.add_argument("--identity-workers", type=int, default=1)
    command.add_argument("--failure-policy", choices=[KEEP_FULL_POLICY],
                         default=KEEP_FULL_POLICY)
    command.add_argument("--image", default=os.getenv(IMAGE_ENV))
    command.add_argument("--task-id")
    command.add_argument("--benchmark-bytes", action="store_true")
    if recover_only_option:
        command.add_argument("--recover-only", action="store_true")


def _build_parser():
    """Build the argument parser with one sub-command per entry of COMMANDS."""
    parser = argparse.ArgumentParser(prog="biomero-shallower")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--returned-zarr", required=True, type=Path)
    migrate = commands.add_parser("migrate-v1")
    migrate.add_argument("--returned-zarr", required=True, type=Path)
    migrate.add_argument("--backup-dir", type=Path)
    for name in ("verify", "normalize", "normalize-tree"):
        _add_store_arguments(commands.add_parser(name),
                             recover_only_option=name == "normalize-tree")
    return parser


def _health(args):
    """Check that the pixel identity library loads and report the capabilities."""
    IsccBioIdentityProvider()._load_upstream()
    return {**capabilities(), "result": RESULT_HEALTHY}


def _inspect(args):
    """List the NGFF image and label nodes of a returned store."""
    return {"schema": INSPECT_SCHEMA,
            "nodes": [asdict(node) for node in discover_ngff_nodes(args.returned_zarr)]}


def _migrate_v1(args):
    """Upgrade one settled schema-1 shallow store."""
    return migrate_shallow_store_v1(
        args.returned_zarr, backup_path=args.backup_dir).to_dict()


def _verify(args, manifest):
    return validate_report(args.returned_zarr, manifest,
                           image=args.image, tool_version=__version__)


def _normalize(args, manifest):
    return normalize(args.returned_zarr, manifest,
                     contract=args.contract_version,
                     identity_workers=args.identity_workers,
                     failure_policy=args.failure_policy, image=args.image,
                     task_id=args.task_id, measure_bytes=args.benchmark_bytes)


def _normalize_tree(args, manifest):
    return normalize_tree(args.returned_zarr, manifest, image=args.image,
                          task_id=args.task_id, workers=args.identity_workers,
                          recover_only=args.recover_only)


def _run_store_command(args, command):
    """Run a command on a store with its canonical inputs and write its report file."""
    if args.report.resolve() == args.canonical_inputs.resolve():
        raise ValueError("Report must not overwrite the canonical input manifest")
    manifest = load_manifest(args.canonical_inputs)
    result = command(args, manifest).to_dict()
    write_json(args.report, result)
    return result


# Commands that need no canonical inputs run directly; the others go through
# `_run_store_command`.
STANDALONE_COMMANDS = {"health": _health, "inspect": _inspect, "migrate-v1": _migrate_v1}
STORE_COMMANDS = {"verify": _verify, "normalize": _normalize,
                  "normalize-tree": _normalize_tree}


def main(argv=None):
    """Run one command; print its JSON result and return the exit code."""
    args = _build_parser().parse_args(argv)
    configure_logging()
    try:
        if args.command in STANDALONE_COMMANDS:
            result = STANDALONE_COMMANDS[args.command](args)
        else:
            result = _run_store_command(args, STORE_COMMANDS[args.command])
        print(json.dumps(result, sort_keys=True))
        return EXIT_OK
    except Exception as exc:
        print(json.dumps({"version": __version__, "result": RESULT_ERROR,
                          "error": str(exc)}), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
