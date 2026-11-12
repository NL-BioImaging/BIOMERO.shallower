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
Noninteractive filesystem CLI. Exit 0: safe terminal; 2: invalid/unsafe.

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
from typing import Any, Callable

from biomero_schema.shallower import (
    SHALLOW_BATCH_REPORT, SHALLOW_OPERATION_REPORT,
    RemoteShallowReceipt, ShallowBatchReport, ShallowOperationReport,
)
from biomero_schema.zarr import SHALLOW_COLLECTION_MANIFEST, CanonicalInputManifest

from . import __version__
from .capabilities import capabilities
from .discovery import discover_ngff_nodes, find_returned_zarr_stores
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


def load_manifest(path: str | Path) -> CanonicalInputManifest:
    """
    Read a canonical input manifest, rejecting a missing or unknown schema.

    Args:
        path (str | Path): The manifest JSON file.

    Returns:
        CanonicalInputManifest:
            The parsed manifest.

    Raises:
        ValueError: If the manifest schema is missing or unknown.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != CANONICAL_MANIFEST_SCHEMA:
        raise ValueError("Missing or unknown canonical manifest contract version")
    return CanonicalInputManifest.from_dict(raw)


def _recover_store(
    store: Path, manifest: CanonicalInputManifest, image: str | None
) -> ShallowOperationReport | None:
    """
    Finish an interrupted prune of ``store`` and return its validated report.

    Returns None for a store that was never shallowed; refuses one that holds a
    shallow manifest without a trusted report.

    Args:
        store (Path): The returned store.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        image (str | None): The expected helper image.

    Returns:
        ShallowOperationReport | None:
            The validated report, or None if the store was never shallowed.

    Raises:
        ValueError: If the store holds a shallow manifest without a trusted report.
    """
    with locked(store):
        recover(store)
    if not (store / SHALLOW_OPERATION_REPORT).exists():
        if (store / SHALLOW_COLLECTION_MANIFEST).exists():
            raise ValueError("Untrusted shallow result cannot fall back")
        return None
    return validate_report(store, manifest, image=image, tool_version=__version__)


def _receipt_for(
    store: Path, root: Path, report: ShallowOperationReport, image: str | None
) -> RemoteShallowReceipt:
    """
    Build the receipt that lets a caller trust the store's committed report.

    Args:
        store (Path): The returned store.
        root (Path): The directory the receipt's artifact path is relative to.
        report (ShallowOperationReport): The store's committed report.
        image (str | None): The helper image to record.

    Returns:
        RemoteShallowReceipt:
            The receipt for the store.
    """
    return RemoteShallowReceipt(
        schema=RECEIPT_SCHEMA, image=image, toolVersion=__version__,
        reportSha256=hashlib.sha256(
            (store / SHALLOW_OPERATION_REPORT).read_bytes()).hexdigest(),
        artifactPath=store.relative_to(root).as_posix(),
        slurmJobId=report.slurm_job_id, taskId=report.task_id,
    )


def normalize_tree(
    root: str | Path,
    manifest: CanonicalInputManifest,
    *,
    image: str | None,
    task_id: str | None,
    workers: int = 1,
    recover_only: bool = False,
) -> ShallowBatchReport:
    """
    Normalize (or only recover) every returned store under ``root``.

    Writes and returns a batch report with a receipt for each store that was
    normalized or whose inputs were refreshed.

    Args:
        root (str | Path): The directory searched for returned Zarr stores.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.
        image (str | None): The helper image recorded in reports and receipts.
        task_id (str | None): The task identifier recorded in reports.
        workers (int, optional): Number of pixel identity workers.
        recover_only (bool, optional): Only finish interrupted prunes instead of
            normalizing.

    Returns:
        ShallowBatchReport:
            The batch report, also written under ``root``.

    Raises:
        ValueError: If a store is untrusted or fails validation.
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


def configure_logging(level: int = LOG_LEVEL, log_format: str = LOG_FORMAT) -> None:
    """
    Log to stderr at ``level`` unless the caller already configured logging.

    Library modules never configure logging. Standard output stays the single
    JSON document, so logs go to stderr (the Slurm job log for helper runs).

    Args:
        level (int, optional): The logging level.
        log_format (str, optional): The log record format.
    """
    logging.basicConfig(level=level, format=log_format)


def _add_store_arguments(command: argparse.ArgumentParser, *, recover_only_option: bool) -> None:
    """
    Add the arguments shared by the commands that work on a store and its inputs.

    Args:
        command (argparse.ArgumentParser): The sub-command parser to extend.
        recover_only_option (bool): Whether to add the ``--recover-only`` flag.
    """
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


def _build_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser with one sub-command per entry of COMMANDS.

    Returns:
        argparse.ArgumentParser:
            The configured parser.
    """
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


def _health(args: argparse.Namespace) -> dict[str, Any]:
    """
    Check that the pixel identity library loads and report the capabilities.

    Args:
        args (argparse.Namespace): The parsed arguments (unused).

    Returns:
        dict[str, Any]:
            The capabilities plus a healthy result.
    """
    IsccBioIdentityProvider()._load_upstream()
    return {**capabilities(), "result": RESULT_HEALTHY}


def _inspect(args: argparse.Namespace) -> dict[str, Any]:
    """
    List the NGFF image and label nodes of a returned store.

    Args:
        args (argparse.Namespace): The parsed arguments.

    Returns:
        dict[str, Any]:
            The inspect document with the discovered nodes.
    """
    return {"schema": INSPECT_SCHEMA,
            "nodes": [asdict(node) for node in discover_ngff_nodes(args.returned_zarr)]}


def _migrate_v1(args: argparse.Namespace) -> dict[str, Any]:
    """
    Upgrade one settled schema-1 shallow store.

    Args:
        args (argparse.Namespace): The parsed arguments.

    Returns:
        dict[str, Any]:
            The migration result document.
    """
    # Imported on use: the legacy migration models are needed by no other command.
    from .migration import migrate_shallow_store_v1
    return migrate_shallow_store_v1(
        args.returned_zarr, backup_path=args.backup_dir).to_dict()


def _verify(args: argparse.Namespace, manifest: CanonicalInputManifest) -> ShallowOperationReport:
    """
    Validate a committed report against its store without rehashing pixels.

    Args:
        args (argparse.Namespace): The parsed arguments.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.

    Returns:
        ShallowOperationReport:
            The validated report.
    """
    return validate_report(args.returned_zarr, manifest,
                           image=args.image, tool_version=__version__)


def _normalize(args: argparse.Namespace, manifest: CanonicalInputManifest) -> ShallowOperationReport:
    """
    Shallow one returned store, or keep it full, and return its report.

    Args:
        args (argparse.Namespace): The parsed arguments.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.

    Returns:
        ShallowOperationReport:
            The operation report.
    """
    return normalize(args.returned_zarr, manifest,
                     contract=args.contract_version,
                     identity_workers=args.identity_workers,
                     failure_policy=args.failure_policy, image=args.image,
                     task_id=args.task_id, measure_bytes=args.benchmark_bytes)


def _normalize_tree(args: argparse.Namespace, manifest: CanonicalInputManifest) -> ShallowBatchReport:
    """
    Normalize or recover every returned store under a directory; return the batch report.

    Args:
        args (argparse.Namespace): The parsed arguments.
        manifest (CanonicalInputManifest): The canonical input snapshot of the run.

    Returns:
        ShallowBatchReport:
            The batch report.
    """
    return normalize_tree(args.returned_zarr, manifest, image=args.image,
                          task_id=args.task_id, workers=args.identity_workers,
                          recover_only=args.recover_only)


def _run_store_command(
    args: argparse.Namespace, command: Callable[[argparse.Namespace, CanonicalInputManifest], Any]
) -> dict[str, Any]:
    """
    Run a command on a store with its canonical inputs and write its report file.

    Args:
        args (argparse.Namespace): The parsed arguments.
        command (Callable[[argparse.Namespace, CanonicalInputManifest], Any]): The
            store command returning a report with ``to_dict``.

    Returns:
        dict[str, Any]:
            The report as a dictionary, also written to ``--report``.

    Raises:
        ValueError: If the report path is the canonical input manifest.
    """
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


def main(argv: list[str] | None = None) -> int:
    """
    Run one command; print its JSON result and return the exit code.

    Args:
        argv (list[str], optional): The command-line arguments; defaults to
            ``sys.argv[1:]``.

    Returns:
        int:
            0 on success, 2 on any error (reported as JSON on stderr).
    """
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
