#!/usr/bin/env python3
"""Safely retire a superseded rolling restart after its successor passes."""

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


def validated_restart(completion: dict, label: str) -> Path:
    if completion.get("status") != "PASS":
        raise SystemExit(f"{label} completion is not PASS")
    restart = completion.get("restart", {})
    path = Path(restart.get("path", ""))
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"{label} restart is missing or empty: {path}")
    if path.stat().st_size != restart.get("size_bytes"):
        raise SystemExit(f"{label} restart size no longer matches its completion")
    if sha256(path) != restart.get("sha256"):
        raise SystemExit(f"{label} restart hash no longer matches its completion")
    return path


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
    parser.add_argument("--previous-completion", required=True, type=Path)
    parser.add_argument("--next-completion", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--preserve-checkpoint",
        action="store_true",
        help="Verify the chain but retain the previous restart as a durable checkpoint.",
    )
    args = parser.parse_args()

    if args.report is not None and Path(f"{args.report}.ready").is_file():
        existing = json.loads(args.report.read_text())
        if (
            existing.get("status") != "PASS"
            or existing.get("previous_completion_sha256")
            != sha256(args.previous_completion)
            or existing.get("next_completion_sha256")
            != sha256(args.next_completion)
        ):
            raise SystemExit(
                "existing restart-retirement publication does not match inputs"
            )
        if existing.get("action") in {"RETIRED", "PRESERVED"}:
            print(json.dumps(existing, indent=2, sort_keys=True))
            return 0
        if existing.get("action") == "READY_TO_RETIRE":
            if not args.execute and not args.preserve_checkpoint:
                print(json.dumps(existing, indent=2, sort_keys=True))
                return 0
        else:
            raise SystemExit(
                "existing restart-retirement publication has an invalid action"
            )

    for completion in (args.previous_completion, args.next_completion):
        if not completion.is_file() or not Path(f"{completion}.ready").is_file():
            raise SystemExit(f"model completion is not published: {completion}")
    previous = json.loads(args.previous_completion.read_text())
    successor = json.loads(args.next_completion.read_text())
    if previous.get("end") != successor.get("start"):
        raise SystemExit("model completions are not adjacent in the same restart chain")
    previous_path = validated_restart(previous, "previous")
    successor_path = validated_restart(successor, "next")
    if previous_path.resolve() == successor_path.resolve():
        raise SystemExit("previous and next completions reference the same restart")

    previous_bytes = previous_path.stat().st_size
    if args.execute and not args.preserve_checkpoint:
        previous_path.unlink()
        action = "RETIRED"
    elif args.preserve_checkpoint:
        action = "PRESERVED"
    else:
        action = "READY_TO_RETIRE"

    result = {
        "schema_version": 1,
        "status": "PASS",
        "action": action,
        "execute": args.execute,
        "previous_end": previous["end"],
        "previous_completion": str(args.previous_completion.resolve()),
        "previous_completion_sha256": sha256(args.previous_completion),
        "previous_restart": str(previous_path.resolve()),
        "previous_restart_bytes": previous_bytes,
        "next_end": successor["end"],
        "next_completion": str(args.next_completion.resolve()),
        "next_completion_sha256": sha256(args.next_completion),
        "next_restart": str(successor_path.resolve()),
    }
    if args.report is not None:
        publish(args.report, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
