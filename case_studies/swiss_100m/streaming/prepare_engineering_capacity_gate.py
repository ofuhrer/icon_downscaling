#!/usr/bin/env python3
"""Publish the event-authorized national 100 m capacity/restart gate plan."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_published(path: Path, label: str) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish(path: Path, payload: str) -> None:
    marker = Path(f"{path}.ready")
    if path.exists() or marker.exists():
        if path.is_file() and marker.is_file() and path.read_text() == payload:
            return
        raise ValueError(f"refusing to replace non-identical publication: {path}")
    write_atomic(path, payload)
    marker.touch()


def forcing_records(
    start: datetime,
    end: datetime,
    forcing_dir: Path,
    reused_paths: dict[str, Path] | None = None,
) -> list[dict]:
    records = []
    reused_paths = reused_paths or {}
    valid = start
    while valid <= end:
        canonical_time = valid.strftime(TIME_FORMAT)
        path = reused_paths.get(
            canonical_time,
            forcing_dir / f"rea_l_hicar_{valid:%Y%m%d_%H%M}.nc",
        )
        records.append(
            {
                "index": len(records),
                "valid_time": canonical_time,
                "cycle_date": valid.strftime("%Y%m%d"),
                "cycle_time": "0000",
                "step_hours": valid.hour,
                "forcing_file": str(path.resolve()),
                "ready_marker": str(Path(f"{path.resolve()}.ready")),
            }
        )
        valid += timedelta(hours=1)
    return records


def publish_chunk(
    gate_root: Path,
    specification: dict,
    shared_restart_dir: Path,
    static_basename: str,
    reused_forcing_paths: dict[str, Path] | None = None,
) -> dict:
    chunk_root = gate_root / specification["id"]
    forcing_dir = chunk_root / "forcing"
    start = datetime.fromisoformat(specification["start"])
    end = datetime.fromisoformat(specification["end"])
    records = forcing_records(
        start,
        end,
        forcing_dir,
        reused_paths=reused_forcing_paths,
    )
    forcing_list_path = chunk_root / "forcing_list.txt"
    plan_path = chunk_root / "chunk_plan.json"
    forcing_list = "".join(
        f'"{record["forcing_file"]}"\n' for record in records
    )
    chunk_payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "chunk_id": f"swiss_100m_capacity_{specification['id']}",
        "start": specification["start"],
        "end": specification["end"],
        "hours": int((end - start).total_seconds() // 3600),
        "record_count": len(records),
        "producer_concurrency": len(records),
        "cycle_policy": (
            "Use the 00 UTC REA-L cycle for 2010-01-01 and step equal to "
            "the valid UTC hour."
        ),
        "transient_policy": (
            "Native GRIB and converter work are job-local; published forcing "
            "is retained through the capacity verdict."
        ),
        "chunk_root": str(chunk_root.resolve()),
        "forcing_list": str(forcing_list_path.resolve()),
        "records": records,
    }
    publish(
        plan_path,
        json.dumps(chunk_payload, indent=2, sort_keys=True) + "\n",
    )
    publish(forcing_list_path, forcing_list)
    end_file = specification["end"].replace("T", "_").replace(":", "-")
    return {
        **specification,
        "hours": chunk_payload["hours"],
        "forcing_record_count": len(records),
        "chunk_plan": str(plan_path.resolve()),
        "forcing_list": str(forcing_list_path.resolve()),
        "run_dir": str((gate_root / "runs" / specification["id"]).resolve()),
        "memory_dir": str(
            (gate_root / "runs" / specification["id"] / "memory").resolve()
        ),
        "timing_report": str(
            (gate_root / "runs" / specification["id"] / "phase_timing.json").resolve()
        ),
        "completion_report": str(
            (
                gate_root
                / "runs"
                / specification["id"]
                / "model_chunk_completion.json"
            ).resolve()
        ),
        "solver_report": str(
            (
                gate_root
                / "runs"
                / specification["id"]
                / "scientific_validation"
                / "solver_log_diagnostics.json"
            ).resolve()
        ),
        "shared_restart_dir": str(shared_restart_dir.resolve()),
        "expected_restart_file": str(
            (shared_restart_dir / f"{static_basename}_{end_file}.nc").resolve()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-config", type=Path, required=True)
    parser.add_argument("--event-assessment", type=Path, required=True)
    parser.add_argument("--geometry-report", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--static-manifest", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    for path, label in (
        (args.event_assessment, "paired-event assessment"),
        (args.geometry_report, "100 m geometry report"),
        (args.static_file, "100 m static file"),
    ):
        require_published(path, label)
    if not args.gate_config.is_file() or not args.static_manifest.is_file():
        raise SystemExit("gate config and static manifest must exist")

    config = load_json(args.gate_config)
    assessment = load_json(args.event_assessment)
    geometry = load_json(args.geometry_report)
    static_manifest = load_json(args.static_manifest)
    required_decision = config["authorization"]["required_event_decision"]
    if (
        assessment.get("assessment_status") != "COMPLETE"
        or assessment.get("decision") != required_decision
        or not assessment.get("authorization", {}).get(
            "100m_engineering_capacity_gate"
        )
    ):
        raise SystemExit("paired-event assessment does not authorize the 100 m gate")
    static_digest = sha256(args.static_file)
    expected_digest = config["case"]["static_sha256"]
    if (
        static_digest != expected_digest
        or static_manifest.get("static_sha256") != expected_digest
    ):
        raise SystemExit("100 m static checksum differs from the frozen gate")
    acceptance = config["acceptance"]
    if (
        geometry.get("status") != "PASS"
        or geometry.get("static_sha256") != static_digest
        or geometry["minimum_mass_jacobian"]["value"]
        < acceptance["minimum_mass_jacobian"]
        or geometry["minimum_interface_layer_thickness"]["value_m"]
        < acceptance["minimum_interface_thickness_m"]
        or geometry["minimum_mass_level_spacing"]["value_m"]
        < acceptance["minimum_mass_level_spacing_m"]
    ):
        raise SystemExit("100 m SLEVE geometry does not meet the frozen gate")

    gate_root = args.gate_root.resolve()
    shared_restart_dir = gate_root / "restart"
    static_basename = args.static_file.stem
    specifications = config["execution"]["segments"]
    first = publish_chunk(
        gate_root,
        specifications[0],
        shared_restart_dir,
        static_basename,
    )
    first_records = load_json(Path(first["chunk_plan"]))["records"]
    shared_boundary = specifications[1]["start"]
    boundary_paths = {
        record["valid_time"]: Path(record["forcing_file"])
        for record in first_records
        if record["valid_time"] == shared_boundary
    }
    if set(boundary_paths) != {shared_boundary}:
        raise SystemExit("initial forcing plan lacks the restart-boundary record")
    second = publish_chunk(
        gate_root,
        specifications[1],
        shared_restart_dir,
        static_basename,
        reused_forcing_paths=boundary_paths,
    )
    segments = [first, second]
    report = (args.output or gate_root / "capacity_gate_plan.json").resolve()
    payload = {
        "schema_version": 1,
        "status": "AUTHORIZED_AND_PLANNED",
        "gate_config": str(args.gate_config.resolve()),
        "gate_config_sha256": sha256(args.gate_config),
        "event_assessment": str(args.event_assessment.resolve()),
        "event_assessment_sha256": sha256(args.event_assessment),
        "event_decision": assessment["decision"],
        "expected_hicar_commit": config["case"]["expected_hicar_commit"],
        "geometry_report": str(args.geometry_report.resolve()),
        "geometry_report_sha256": sha256(args.geometry_report),
        "static_file": str(args.static_file.resolve()),
        "static_sha256": static_digest,
        "gate_root": str(gate_root),
        "segments": segments,
        "boundary_comparison_report": str(
            (gate_root / "validation" / "restart_boundary_comparison.json").resolve()
        ),
        "accounting_report": str(
            (gate_root / "validation" / "slurm_accounting.psv").resolve()
        ),
        "capacity_assessment_report": str(
            (gate_root / "validation" / "capacity_gate_assessment.json").resolve()
        ),
        "interpretation": config["interpretation"],
    }
    publish(report, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"100 m capacity gate plan published: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
