#!/usr/bin/env python3
"""Publish per-case forcing lists and manifests for wind-pathway replays."""

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


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or Path(f"{path}.ready").exists():
        raise ValueError(f"refusing to replace publication: {path}")
    atomic_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    Path(f"{path}.ready").touch()


def finalize(plan_path: Path) -> dict[str, Any]:
    if not plan_path.is_file() or not Path(f"{plan_path}.ready").is_file():
        raise ValueError(f"experiment plan is not published: {plan_path}")
    experiment = json.loads(plan_path.read_text())
    summaries = []
    for case in experiment["cases"]:
        forcing_list = Path(case["forcing_list"])
        model_plan_path = Path(case["model_plan"])
        publication_path = Path(case["forcing_publication"])
        for path in (forcing_list, model_plan_path, publication_path):
            if path.exists() or Path(f"{path}.ready").exists():
                raise ValueError(f"refusing to replace publication: {path}")
        entries = []
        total_bytes = 0
        for record in case["records"]:
            forcing = Path(record["forcing_file"])
            manifest = forcing.with_suffix(".manifest.json")
            validation = forcing.with_suffix(".validation.json")
            for path in (
                forcing, Path(f"{forcing}.ready"), manifest, validation
            ):
                if not path.is_file():
                    raise ValueError(f"forcing artifact is missing: {path}")
            metadata = json.loads(manifest.read_text())
            checked = json.loads(validation.read_text())
            if metadata.get("status") != "PASS" or checked.get("status") != "PASS":
                raise ValueError(f"forcing record is not PASS: {forcing}")
            if metadata.get("valid_time") != record["valid_time"]:
                raise ValueError(f"forcing valid time mismatch: {forcing}")
            if metadata.get("forcing_sha256") != sha256(forcing):
                raise ValueError(f"forcing checksum mismatch: {forcing}")
            total_bytes += forcing.stat().st_size
            entries.append(
                {
                    "index": record["index"],
                    "valid_time": record["valid_time"],
                    "cycle_date": record["cycle_date"],
                    "step_hours": record["step_hours"],
                    "forcing_file": str(forcing),
                    "forcing_sha256": metadata["forcing_sha256"],
                    "forcing_size_bytes": forcing.stat().st_size,
                    "record_manifest": str(manifest),
                    "record_manifest_sha256": sha256(manifest),
                    "validation_report": str(validation),
                    "validation_report_sha256": sha256(validation),
                    "stage_seconds": metadata.get("stage_seconds", {}),
                }
            )
        atomic_text(
            forcing_list,
            "".join(f'"{entry["forcing_file"]}"\n' for entry in entries),
        )
        Path(f"{forcing_list}.ready").touch()
        model_plan = {
            "schema_version": 1,
            "status": "PLANNED",
            "chunk_id": f"wind-pathway-{case['case_id']}",
            "chunk_root": case["case_root"],
            "start": case["start"],
            "end": case["end"],
            "hours": case["hours"],
            "forcing_list": str(forcing_list),
            "record_count": len(entries),
            "records": case["records"],
        }
        publish(model_plan_path, model_plan)
        publication = {
            "schema_version": 1,
            "status": "PASS",
            "chunk_id": model_plan["chunk_id"],
            "start": case["start"],
            "end": case["end"],
            "hours": case["hours"],
            "records": len(entries),
            "expected_records": len(entries),
            "entries": entries,
            "forcing_list": str(forcing_list),
            "forcing_list_sha256": sha256(forcing_list),
            "plan": str(model_plan_path),
            "plan_sha256": sha256(model_plan_path),
            "total_forcing_bytes": total_bytes,
            "failures": [],
        }
        publish(publication_path, publication)
        summaries.append(
            {
                "case_id": case["case_id"],
                "records": len(entries),
                "total_forcing_bytes": total_bytes,
            }
        )
    result = {
        "schema_version": 1,
        "status": "PASS",
        "experiment_plan": str(plan_path),
        "experiment_plan_sha256": sha256(plan_path),
        "cases": summaries,
    }
    publish(
        Path(experiment["output_root"]) / "forcing_finalization.json",
        result,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-plan", type=Path, required=True)
    args = parser.parse_args()
    result = finalize(args.experiment_plan.resolve())
    print(f"pathway forcing finalized: {len(result['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
