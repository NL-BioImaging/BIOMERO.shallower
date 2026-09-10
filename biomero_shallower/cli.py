"""Noninteractive filesystem CLI. Exit 0: safe terminal; 2: invalid/unsafe."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from biomero_schema.shallower import (
    SHALLOW_BATCH_REPORT, SHALLOW_OPERATION_REPORT,
    RemoteShallowReceipt, ShallowBatchReport,
)
from biomero_schema.zarr import CanonicalInputManifest

from . import __version__
from .operations import ADAPTERS, locked, normalize, validate_report
from .result_zarr import discover_ngff_nodes, find_returned_zarr_stores
from .transaction import recover, write_json


def load_manifest(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != 1:
        raise ValueError("Missing or unknown canonical manifest contract version")
    return CanonicalInputManifest.from_dict(raw)


def normalize_tree(root, manifest, *, image, task_id, workers=1,
                   recover_only=False):
    root = Path(root)
    receipts = []
    for store in find_returned_zarr_stores(root):
        if recover_only:
            with locked(store):
                recover(store)
            if not (store / SHALLOW_OPERATION_REPORT).exists():
                if (store / ".biomero-shallow.json").exists():
                    raise ValueError("Untrusted shallow result cannot fall back")
                continue
            report = validate_report(store, manifest, image=image,
                                     tool_version=__version__)
        else:
            report = normalize(store, manifest, image=image, task_id=task_id,
                               identity_workers=workers)
        if report.result == "normalized":
            receipts.append(RemoteShallowReceipt(
                schema=1, image=image, toolVersion=__version__,
                reportSha256=hashlib.sha256(
                    (store / SHALLOW_OPERATION_REPORT).read_bytes()).hexdigest(),
                artifactPath=store.relative_to(root).as_posix(),
                slurmJobId=report.slurm_job_id, taskId=report.task_id,
            ))
    batch = ShallowBatchReport(schema=1, canonicalInputs=manifest, image=image,
                               toolVersion=__version__, result="complete",
                               receipts=tuple(receipts))
    write_json(root / SHALLOW_BATCH_REPORT, batch.to_dict())
    return batch


def main(argv=None):
    parser = argparse.ArgumentParser(prog="biomero-shallower")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--returned-zarr", required=True, type=Path)
    for name in ("verify", "normalize", "normalize-tree"):
        command = commands.add_parser(name)
        command.add_argument("--returned-zarr", required=True, type=Path)
        command.add_argument("--canonical-inputs", required=True, type=Path)
        command.add_argument("--contract-version", required=True, type=int, choices=ADAPTERS)
        command.add_argument("--report", required=True, type=Path)
        command.add_argument("--identity-workers", type=int, default=1)
        command.add_argument("--failure-policy", choices=["keep-full"], default="keep-full")
        command.add_argument("--image", default=os.getenv("BIOMERO_SHALLOWER_IMAGE"))
        command.add_argument("--task-id")
        command.add_argument("--benchmark-bytes", action="store_true")
        if name == "normalize-tree":
            command.add_argument("--recover-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "health":
            from .pixel_identity import IsccBioIdentityProvider
            IsccBioIdentityProvider()._load_upstream()
            result = {"version": __version__, "contracts": list(ADAPTERS), "result": "healthy"}
        elif args.command == "inspect":
            from dataclasses import asdict
            result = {"schema": 1, "nodes": [asdict(node) for node in
                                             discover_ngff_nodes(args.returned_zarr)]}
        else:
            if args.report.resolve() == args.canonical_inputs.resolve():
                raise ValueError("Report must not overwrite the canonical input manifest")
            manifest = load_manifest(args.canonical_inputs)
            if args.command == "verify":
                report = validate_report(args.returned_zarr, manifest,
                                         image=args.image, tool_version=__version__)
            elif args.command == "normalize-tree":
                report = normalize_tree(args.returned_zarr, manifest, image=args.image,
                                        task_id=args.task_id, workers=args.identity_workers,
                                        recover_only=args.recover_only)
            else:
                report = normalize(args.returned_zarr, manifest,
                                   contract=args.contract_version,
                                   identity_workers=args.identity_workers,
                                   failure_policy=args.failure_policy, image=args.image,
                                   task_id=args.task_id, measure_bytes=args.benchmark_bytes)
            result = report.to_dict()
            write_json(args.report, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"version": __version__, "result": "error",
                          "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
