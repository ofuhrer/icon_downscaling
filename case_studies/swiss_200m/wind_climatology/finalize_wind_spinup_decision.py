#!/usr/bin/env python3
"""Apply the fail-closed reference-bracketing rule to a published assessment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_published(path: Path) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"assessment is not published: {path}")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def finalize(source_path: Path, output_path: Path) -> dict[str, Any]:
    require_published(source_path)
    source = json.loads(source_path.read_text())
    if source.get("schema_version") != 1:
        raise ValueError("unsupported wind-spinup assessment schema")
    reference = int(source["reference_spinup_hours"])
    passing = {
        int(hours): bool(value)
        for hours, value in source["pass_by_spinup_hours"].items()
    }
    shorter = sorted(hours for hours in passing if hours < reference)
    if not passing.get(reference):
        status = "HOLD"
        decision = "REFERENCE_FAILED_PHYSICALITY"
        selected = None
        lower_bound = None
    else:
        selected = next(
            (
                hours
                for hours in sorted(passing)
                if all(passing[later] for later in passing if later >= hours)
            ),
            None,
        )
        if selected is None:
            status = "HOLD"
            decision = "NO_SPINUP_SELECTED"
            lower_bound = None
        elif selected == reference:
            status = "HOLD"
            decision = "MINIMUM_SPINUP_NOT_BRACKETED"
            lower_bound = reference
            selected = None
        else:
            status = "PASS"
            decision = "SELECT_MINIMUM_SPINUP"
            lower_bound = None
    payload = {
        **source,
        "status": status,
        "decision": decision,
        "selected_spinup_hours": selected,
        "lower_bound_spinup_hours": lower_bound,
        "source_assessment": str(source_path),
        "source_assessment_sha256": sha256(source_path),
        "bracketing_rule": (
            "The longest tested spin-up is the reference, so its self-comparison "
            "cannot establish convergence. At least one shorter member and its "
            "entire longer-spin-up tail must pass."
        ),
        "shorter_tested_spinup_hours": shorter,
    }
    if output_path.exists() or Path(f"{output_path}.ready").exists():
        raise ValueError(f"refusing to replace existing final decision: {output_path}")
    write_json_atomic(output_path, payload)
    Path(f"{output_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = finalize(args.assessment.resolve(), args.output.resolve())
    print(
        f"wind-spinup decision: {payload['status']} "
        f"decision={payload['decision']} "
        f"selected={payload['selected_spinup_hours']} "
        f"lower_bound={payload['lower_bound_spinup_hours']}"
    )
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
