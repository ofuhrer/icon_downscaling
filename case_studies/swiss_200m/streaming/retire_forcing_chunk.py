#!/usr/bin/env python3
"""Safely retire large forcing payloads after a model chunk is validated."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = Path(f"{path}.ready")
    marker.unlink(missing_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    marker.touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--forcing-publication", required=True, type=Path)
    parser.add_argument("--model-completion", required=True, type=Path)
    parser.add_argument(
        "--report",
        type=Path,
        help="Atomically publish the retirement result and ready marker.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete verified forcing and cycle-static payloads; default is a dry run.",
    )
    args = parser.parse_args()

    if args.report is not None and Path(f"{args.report}.ready").is_file():
        existing = json.loads(args.report.read_text())
        if (
            existing.get("status") != "PASS"
            or existing.get("plan_sha256") != sha256(args.plan)
            or existing.get("forcing_publication_sha256")
            != sha256(args.forcing_publication)
            or existing.get("model_completion_sha256")
            != sha256(args.model_completion)
        ):
            raise SystemExit(
                "existing retirement publication does not match supplied inputs"
            )
        if existing.get("action") == "RETIRED" and existing.get("execute"):
            print(json.dumps(existing, indent=2, sort_keys=True))
            return 0
        if (
            existing.get("action") == "READY_TO_RETIRE"
            and not existing.get("execute")
        ):
            if not args.execute:
                print(json.dumps(existing, indent=2, sort_keys=True))
                return 0
        else:
            raise SystemExit(
                "existing retirement publication has an invalid action"
            )

    for report in (args.forcing_publication, args.model_completion):
        marker = Path(f"{report}.ready")
        if not report.is_file() or not marker.is_file():
            raise SystemExit(f"publication is incomplete: {report}")
    plan = json.loads(args.plan.read_text())
    forcing = json.loads(args.forcing_publication.read_text())
    model = json.loads(args.model_completion.read_text())
    if forcing.get("status") != "PASS" or model.get("status") != "PASS":
        raise SystemExit("forcing and model publications must both be PASS")
    if forcing.get("chunk_id") != plan["chunk_id"] or model.get("chunk_id") != plan["chunk_id"]:
        raise SystemExit("chunk identifiers disagree")
    if forcing.get("plan_sha256") != sha256(args.plan):
        raise SystemExit("forcing publication does not identify the supplied plan")
    provenance = model.get("provenance", {})
    if provenance.get("status") == "PASS":
        if provenance.get("plan_sha256") != sha256(args.plan):
            raise SystemExit("model provenance does not identify the supplied plan")
        if provenance.get("forcing_publication_sha256") != sha256(
            args.forcing_publication
        ):
            raise SystemExit(
                "model provenance does not identify the forcing publication"
            )

    publications = {entry["forcing_file"]: entry for entry in forcing["entries"]}
    payloads: list[Path] = []
    markers: list[Path] = [Path(f"{args.forcing_publication}.ready")]
    total = 0
    for record in plan["records"]:
        path = Path(record["forcing_file"])
        entry = publications.get(str(path))
        if entry is None:
            raise SystemExit(f"forcing publication lacks planned record: {path}")
        if not path.is_file() or sha256(path) != entry["forcing_sha256"]:
            raise SystemExit(f"forcing payload does not match publication: {path}")
        ready = Path(f"{path}.ready")
        if not ready.is_file():
            raise SystemExit(f"forcing payload is not published: {path}")
        payloads.append(path)
        markers.append(ready)
        total += path.stat().st_size

    cache = Path(plan["chunk_root"]) / "cache"
    cache_files: list[Path] = []
    if cache.exists():
        for path in sorted(cache.rglob("*"), reverse=True):
            if path.is_file():
                cache_files.append(path)

    action = "READY_TO_RETIRE"
    if args.execute:
        # Withdraw every publication marker before removing any payload.
        for path in markers:
            path.unlink()
        for path in payloads:
            path.unlink()
        for path in cache_files:
            path.unlink()
        if cache.exists():
            for directory in sorted(
                (path for path in cache.rglob("*") if path.is_dir()), reverse=True
            ):
                directory.rmdir()
            cache.rmdir()
        action = "RETIRED"

    result = {
        "schema_version": 1,
        "status": "PASS",
        "action": action,
        "chunk_id": plan["chunk_id"],
        "execute": args.execute,
        "payload_bytes": total,
        "payload_count": len(payloads),
        "cache_file_count": len(cache_files),
        "plan": str(args.plan.resolve()),
        "plan_sha256": sha256(args.plan),
        "forcing_publication": str(args.forcing_publication.resolve()),
        "forcing_publication_sha256": sha256(args.forcing_publication),
        "model_completion": str(args.model_completion.resolve()),
        "model_completion_sha256": sha256(args.model_completion),
        "forcing_publication_ready_withdrawn": args.execute,
        "targets": [str(path.resolve()) for path in payloads],
    }
    if args.report is not None:
        publish(args.report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
