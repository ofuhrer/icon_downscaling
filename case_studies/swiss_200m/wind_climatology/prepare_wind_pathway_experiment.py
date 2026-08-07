#!/usr/bin/env python3
"""Plan bounded restart replays that screen HICAR wind-memory pathways."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or Path(f"{path}.ready").exists():
        raise ValueError(f"refusing to replace publication: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    Path(f"{path}.ready").touch()


def source_state(report: dict[str, Any], age: int) -> dict[str, Any]:
    if age == report["reference_spinup_hours"]:
        restart = Path(report["reference_restart"])
        completion = Path(report["reference_completion"])
    else:
        comparison = next(
            item for item in report["comparisons"]
            if item["spinup_hours"] == age
        )
        restart = Path(comparison["restart"])
        completion = Path(comparison["completion"])
    if not restart.is_file() or not completion.is_file():
        raise ValueError(f"preserved state is missing for age {age}")
    completed = json.loads(completion.read_text())
    static = Path(completed["provenance"]["static_file"])
    if not static.is_file() or not Path(f"{static}.ready").is_file():
        raise ValueError(f"static publication is missing: {static}")
    return {
        "spinup_hours": age,
        "restart": str(restart),
        "restart_sha256": sha256(restart),
        "completion": str(completion),
        "completion_sha256": sha256(completion),
        "static_file": str(static),
        "static_sha256": sha256(static),
    }


def forcing_record(
    root: Path, case_id: str, valid_time: datetime, index: int
) -> dict[str, Any]:
    forcing = (
        root / "forcing" / case_id
        / f"rea_l_hicar_{valid_time:%Y%m%d_%H%M}.nc"
    )
    return {
        "index": index,
        "cycle_date": valid_time.strftime("%Y%m%d"),
        "cycle_time": "0000",
        "step_hours": valid_time.hour,
        "valid_time": valid_time.isoformat(),
        "forcing_file": str(forcing),
        "ready_marker": f"{forcing}.ready",
    }


def prepare(
    mechanism_dir: Path,
    output_root: Path,
    case_ids: list[str],
    duration_hours: int = 2,
    include_density_screen: bool = False,
) -> dict[str, Any]:
    if duration_hours < 2:
        raise ValueError(
            "pathway replay must extend past the first hourly wind-update "
            "boundary; use at least two hours"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    producer_records: list[dict[str, Any]] = []
    cases = []
    runs = []
    for case_id in case_ids:
        report_path = mechanism_dir / f"{case_id}.json"
        if not report_path.is_file() or not Path(
            f"{report_path}.ready"
        ).is_file():
            raise ValueError(f"mechanism report is not published: {report_path}")
        report = json.loads(report_path.read_text())
        start = datetime.fromisoformat(report["final_valid_time"])
        end = start + timedelta(hours=duration_hours)
        case_root = output_root / "cases" / case_id
        records = []
        for hour in range(duration_hours + 1):
            valid_time = start + timedelta(hours=hour)
            record = forcing_record(
                output_root, case_id, valid_time, len(producer_records)
            )
            producer_records.append(record)
            records.append(dict(record))
        forcing_list = case_root / "forcing_list.txt"
        model_plan = case_root / "chunk_plan.json"
        states = [source_state(report, age) for age in (24, 48)]
        case = {
            "case_id": case_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "hours": duration_hours,
            "case_root": str(case_root),
            "forcing_list": str(forcing_list),
            "model_plan": str(model_plan),
            "forcing_publication": str(case_root / "forcing_publication.json"),
            "records": records,
            "source_states": states,
            "mechanism_report": str(report_path),
            "mechanism_report_sha256": sha256(report_path),
        }
        cases.append(case)
        for state in states:
            for sx in ("on", "off"):
                densities = (
                    ("on", "off") if include_density_screen else ("on",)
                )
                for density in densities:
                    run_id = (
                        f"{case_id}-{state['spinup_hours']:02d}h"
                        f"-sx-{sx}-density-{density}"
                    )
                    runs.append(
                        {
                            "index": len(runs),
                            "run_id": run_id,
                            "case_id": case_id,
                            "spinup_hours": state["spinup_hours"],
                            "sx": sx,
                            "advect_density": density,
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                            "model_plan": str(model_plan),
                            "run_dir": str(output_root / "runs" / run_id),
                            **{
                                key: state[key]
                                for key in (
                                    "restart",
                                    "restart_sha256",
                                    "static_file",
                                    "static_sha256",
                                )
                            },
                        }
                    )
    producer_plan = {
        "schema_version": 1,
        "status": "PLANNED",
        "purpose": "wind-pathway-forcing",
        "chunk_id": "wind-pathway-forcing",
        "chunk_root": str(output_root),
        "producer_root": str(output_root),
        "records": producer_records,
    }
    producer_path = output_root / "producer_plan.json"
    publish_json(producer_path, producer_plan)
    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "purpose": "wind-spinup-pathway-factorial",
        "scope": (
            "Replay extends beyond the first hourly wind-update boundary; "
            "Sx is isolated directly. "
            "advect_density screens the combined density-weighted advection "
            "and projection pathway and is not a projection-only intervention."
        ),
        "duration_hours": duration_hours,
        "density_screen_included": include_density_screen,
        "output_root": str(output_root),
        "producer_plan": str(producer_path),
        "cases": cases,
        "runs": runs,
    }
    publish_json(output_root / "experiment_plan.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mechanism-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        default=[],
        help="case to include; defaults to plateau and Sabine",
    )
    parser.add_argument("--duration-hours", type=int, default=2)
    parser.add_argument(
        "--include-density-screen",
        action="store_true",
        help="include the unqualified combined density pathway screen",
    )
    args = parser.parse_args()
    payload = prepare(
        args.mechanism_dir.resolve(),
        args.output_root.resolve(),
        args.case_ids
        or ["plateau-inversion", "sabine-strong-wind"],
        args.duration_hours,
        args.include_density_screen,
    )
    print(
        f"wind pathway experiment planned: "
        f"{len(payload['cases'])} cases, {len(payload['runs'])} runs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
