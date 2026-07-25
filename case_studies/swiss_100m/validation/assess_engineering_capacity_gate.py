#!/usr/bin/env python3
"""Apply the frozen national 100 m engineering-capacity acceptance criteria."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile


TIMER = re.compile(
    r"^\s*(total|init|input|output|physics|forcing|wind bal|winds):"
    r"\s+([0-9.]+)\s+\|\s+([0-9.]+)\s+\|\s+([0-9.]+)\s*$",
    re.MULTILINE,
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def published(path: Path, label: str, failures: list[str]) -> dict:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        failures.append(f"{label} is not published: {path}")
        return {}
    try:
        payload = load_json(path)
    except Exception as exc:
        failures.append(f"{label} is unreadable: {exc}")
        return {}
    if payload.get("status") != "PASS":
        failures.append(f"{label} is not PASS")
    return payload


def key_values(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        if "=" not in line:
            raise ValueError(f"invalid memory sample line in {path}: {line!r}")
        key, value = line.split("=", 1)
        result[key] = value
    return result


def memory_summary(
    directory: Path,
    expected_gpus: int,
    expected_nodes: int,
    minimum_headroom: float,
    failures: list[str],
) -> dict:
    gpu_records = []
    for path in sorted(directory.glob("gpu_rank_*.txt")):
        try:
            record = key_values(path)
            peak = int(record["peak_gpu_memory_mib"])
            total = int(record["total_gpu_memory_mib"])
            headroom = (total - peak) / total
            gpu_records.append(
                {
                    "path": str(path.resolve()),
                    "rank": int(record["rank"]),
                    "host": record["host"],
                    "gpu_index": int(record["gpu_index"]),
                    "peak_mib": peak,
                    "total_mib": total,
                    "headroom_fraction": headroom,
                }
            )
        except Exception as exc:
            failures.append(f"invalid GPU memory sample {path}: {exc}")
    node_records = []
    for path in sorted(directory.glob("node_*.txt")):
        try:
            record = key_values(path)
            total = int(record["total_memory_kib"])
            available = int(record["minimum_available_memory_kib"])
            node_records.append(
                {
                    "path": str(path.resolve()),
                    "host": record["host"],
                    "total_kib": total,
                    "minimum_available_kib": available,
                    "headroom_fraction": available / total,
                }
            )
        except Exception as exc:
            failures.append(f"invalid node memory sample {path}: {exc}")
    if len(gpu_records) != expected_gpus:
        failures.append(
            f"{directory} has {len(gpu_records)} GPU samples; expected {expected_gpus}"
        )
    if len(node_records) != expected_nodes:
        failures.append(
            f"{directory} has {len(node_records)} node samples; expected {expected_nodes}"
        )
    if len({item["rank"] for item in gpu_records}) != len(gpu_records):
        failures.append(f"{directory} has duplicate GPU rank samples")
    if len({item["host"] for item in node_records}) != len(node_records):
        failures.append(f"{directory} has duplicate node samples")
    expected_node_hosts = {item["host"] for item in node_records}
    gpu_node_hosts = {item["host"] for item in gpu_records}
    gpus_per_node = (
        expected_gpus // expected_nodes if expected_nodes > 0 else 0
    )
    expected_gpu_indices = set(range(gpus_per_node))
    topology_valid = (
        expected_nodes > 0
        and expected_gpus == expected_nodes * gpus_per_node
        and {item["rank"] for item in gpu_records} == set(range(expected_gpus))
        and gpu_node_hosts == expected_node_hosts
        and all(
            {
                item["gpu_index"]
                for item in gpu_records
                if item["host"] == host
            }
            == expected_gpu_indices
            for host in expected_node_hosts
        )
    )
    if not topology_valid:
        failures.append(
            f"{directory} does not contain the exact expected node/GPU topology"
        )
    low_gpus = [
        item for item in gpu_records if item["headroom_fraction"] < minimum_headroom
    ]
    low_nodes = [
        item for item in node_records if item["headroom_fraction"] < minimum_headroom
    ]
    if low_gpus:
        failures.append(
            f"{len(low_gpus)} GPUs have less than {minimum_headroom:.0%} headroom"
        )
    if low_nodes:
        failures.append(
            f"{len(low_nodes)} nodes have less than {minimum_headroom:.0%} headroom"
        )
    return {
        "directory": str(directory.resolve()),
        "gpu_sample_count": len(gpu_records),
        "node_sample_count": len(node_records),
        "minimum_gpu_headroom_fraction": min(
            (item["headroom_fraction"] for item in gpu_records), default=None
        ),
        "minimum_node_headroom_fraction": min(
            (item["headroom_fraction"] for item in node_records), default=None
        ),
        "maximum_gpu_peak_mib": max(
            (item["peak_mib"] for item in gpu_records), default=None
        ),
        "topology_valid": topology_valid,
        "node_hosts": sorted(expected_node_hosts),
        "gpu_node_hosts": sorted(gpu_node_hosts),
        "expected_gpu_indices_per_node": sorted(expected_gpu_indices),
        "gpu_records": gpu_records,
        "node_records": node_records,
    }


def timing_summary(model_log: Path) -> dict:
    text = model_log.read_text(errors="replace")
    timers = {
        match.group(1): {
            "mean_seconds": float(match.group(2)),
            "minimum_seconds": float(match.group(3)),
            "maximum_seconds": float(match.group(4)),
        }
        for match in TIMER.finditer(text)
    }
    return {"model_log": str(model_log.resolve()), "hicar_timers": timers}


def accounting_summary(path: Path, job_ids: dict[str, str]) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8"), delimiter="|"))
    selected = {}
    for label, job_id in job_ids.items():
        matches = [
            row
            for row in rows
            if row.get("JobIDRaw") == job_id
            or row.get("JobIDRaw", "").startswith(f"{job_id}_")
        ]
        selected[label] = matches
    return {
        "path": str(path.resolve()),
        "job_ids": job_ids,
        "records": selected,
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--accounting", type=Path, required=True)
    parser.add_argument("--job", action="append", default=[], metavar="LABEL=ID")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    failures: list[str] = []
    if not args.plan.is_file() or not Path(f"{args.plan}.ready").is_file():
        raise SystemExit("capacity plan is not published")
    plan = load_json(args.plan)
    config_path = Path(plan["gate_config"])
    config = load_json(config_path)
    acceptance = config["acceptance"]
    config_digest = sha256(config_path)
    if plan.get("gate_config_sha256") != config_digest:
        failures.append("capacity plan does not hash the frozen gate config")
    if plan.get("status") != "AUTHORIZED_AND_PLANNED":
        failures.append("capacity plan is not authorized")
    required_event_decision = config["authorization"][
        "required_event_decision"
    ]
    if plan.get("event_decision") != required_event_decision:
        failures.append("capacity plan does not contain the required event decision")
    event_path = Path(plan.get("event_assessment", ""))
    event = {}
    if not event_path.is_file() or not Path(f"{event_path}.ready").is_file():
        failures.append("paired-event assessment is not published")
    else:
        event = load_json(event_path)
        if (
            plan.get("event_assessment_sha256") != sha256(event_path)
            or event.get("assessment_status") != "COMPLETE"
            or event.get("decision") != required_event_decision
        ):
            failures.append(
                "paired-event assessment does not verify the required authorization"
            )
    expected_commit = config["case"]["expected_hicar_commit"]
    expected_static_sha256 = config["case"]["static_sha256"]
    if plan.get("expected_hicar_commit") != expected_commit:
        failures.append("capacity plan source commit differs from the frozen config")
    static_path = Path(plan.get("static_file", ""))
    actual_static_sha256 = (
        sha256(static_path) if static_path.is_file() else None
    )
    if (
        not isinstance(plan.get("static_sha256"), str)
        or not HEX_64.fullmatch(plan["static_sha256"])
        or plan["static_sha256"] != expected_static_sha256
        or actual_static_sha256 != expected_static_sha256
    ):
        failures.append("capacity plan does not verify the frozen static domain")

    geometry_path = Path(plan["geometry_report"])
    geometry = published(geometry_path, "geometry report", failures)
    if geometry:
        if (
            plan.get("geometry_report_sha256") != sha256(geometry_path)
            or geometry.get("static_sha256") != expected_static_sha256
            or geometry.get("terrain_shape")
            != config["case"]["horizontal_shape_yx"]
        ):
            failures.append(
                "geometry report does not match the frozen national static domain"
            )
        if (
            geometry["minimum_mass_jacobian"]["value"]
            < acceptance["minimum_mass_jacobian"]
            or geometry["minimum_interface_layer_thickness"]["value_m"]
            < acceptance["minimum_interface_thickness_m"]
            or geometry["minimum_mass_level_spacing"]["value_m"]
            < acceptance["minimum_mass_level_spacing_m"]
        ):
            failures.append("independent SLEVE geometry is below the frozen margin")

    segment_results = []
    combined_times = []
    restart_sizes = []
    model_identities = set()
    for segment in plan["segments"]:
        name = segment["id"]
        completion = published(
            Path(segment["completion_report"]), f"{name} completion", failures
        )
        solver = published(
            Path(segment["solver_report"]), f"{name} solver audit", failures
        )
        timing = published(
            Path(segment["timing_report"]), f"{name} timing", failures
        )
        memory = memory_summary(
            Path(segment["memory_dir"]),
            int(acceptance["expected_compute_gpu_records_per_segment"]),
            int(acceptance["expected_node_records_per_segment"]),
            float(acceptance["minimum_memory_headroom_fraction_every_gpu"]),
            failures,
        )
        if memory["minimum_node_headroom_fraction"] is not None and (
            memory["minimum_node_headroom_fraction"]
            < float(acceptance["minimum_memory_headroom_fraction_every_node"])
        ):
            failures.append(f"{name} node headroom is below the frozen margin")
        if completion:
            combined_times.extend(completion["output"]["times"])
            restart_sizes.append(completion["restart"]["size_bytes"])
            if (
                int(completion.get("output", {}).get("size_bytes", 0)) <= 0
                or int(completion.get("restart", {}).get("size_bytes", 0)) <= 0
            ):
                failures.append(f"{name} does not record positive output/restart bytes")
            if completion.get("provenance", {}).get("status") != "PASS":
                failures.append(f"{name} production provenance is not PASS")
            provenance = completion.get("provenance", {})
            if provenance.get("source_commit") != expected_commit:
                failures.append(
                    f"{name} source commit differs from the frozen capacity gate"
                )
            if provenance.get("static_sha256") != expected_static_sha256:
                failures.append(
                    f"{name} static domain differs from the frozen capacity gate"
                )
            model_identities.add(
                (
                    provenance.get("source_commit"),
                    provenance.get("executable_sha256"),
                    provenance.get("static_sha256"),
                )
            )
            if completion.get("restart_continuation") != segment["restart_continuation"]:
                failures.append(f"{name} continuation flag differs from the plan")
        if solver:
            sleve = solver.get("sleve_geometry", [])
            if len(sleve) != 1:
                failures.append(f"{name} solver audit lacks one SLEVE gate")
            elif (
                sleve[0]["minimum_mass_jacobian"]
                < acceptance["minimum_mass_jacobian"]
                or sleve[0]["minimum_interface_thickness_m"]
                < acceptance["minimum_interface_thickness_m"]
            ):
                failures.append(f"{name} runtime SLEVE geometry is below the margin")
            conservation = (
                solver.get("adjoint_conservation", {})
                .get("relative_Bq", {})
                .get("maximum")
            )
            if conservation is None or conservation > acceptance["maximum_adjoint_conservation"]:
                failures.append(f"{name} adjoint conservation exceeds the margin")
        if timing:
            required_timing_fields = (
                "model_wall_seconds",
                "validation_wall_seconds",
                "restart_write_wall_upper_bound_seconds",
            )
            if not all(
                isinstance(timing.get(field), (int, float))
                and math.isfinite(float(timing[field]))
                and float(timing[field]) > 0.0
                for field in required_timing_fields
            ):
                failures.append(f"{name} lacks complete measured phase timing")
            if timing.get("model_wall_seconds", math.inf) > acceptance[
                "maximum_model_wall_seconds_per_segment"
            ]:
                failures.append(f"{name} exceeds the model wall envelope")
        model_log = Path(completion["model_log"]) if completion else None
        parsed_timing = timing_summary(model_log) if model_log and model_log.is_file() else {}
        required_hicar_timers = {
            "total",
            "init",
            "input",
            "output",
            "physics",
            "forcing",
            "wind bal",
            "winds",
        }
        if parsed_timing and set(
            parsed_timing.get("hicar_timers", {})
        ) != required_hicar_timers:
            failures.append(f"{name} lacks the complete HICAR timer inventory")
        if segment["restart_continuation"] and model_log:
            text = model_log.read_text(errors="replace")
            if "Reading restart data" not in text:
                failures.append(f"{name} has no restart-read marker")
        segment_results.append(
            {
                "id": name,
                "completion": completion,
                "solver": solver,
                "phase_timing": timing,
                "memory": memory,
                **parsed_timing,
            }
        )

    expected_times = acceptance["required_combined_output_times"]
    if combined_times != expected_times or len(combined_times) != len(set(combined_times)):
        failures.append("combined output times are not the exact unique frozen sequence")
    if not restart_sizes or any(not value for value in restart_sizes):
        failures.append("one or more exact-end restart files are empty")
    if (
        len(model_identities) != 1
        or None in next(iter(model_identities), (None,))
    ):
        failures.append(
            "capacity segments do not share one source, executable, and static identity"
        )

    boundary = published(
        Path(plan["boundary_comparison_report"]),
        "restart boundary comparison",
        failures,
    )
    job_ids = {}
    for value in args.job:
        if "=" not in value:
            raise SystemExit(f"--job must be LABEL=ID, got {value!r}")
        label, job_id = value.split("=", 1)
        job_ids[label] = job_id
    required_accounting_labels = set(
        acceptance["required_accounting_job_labels"]
    )
    if set(job_ids) != required_accounting_labels:
        failures.append(
            "Slurm accounting labels do not match the frozen capacity DAG"
        )
    accounting = accounting_summary(args.accounting, job_ids)
    for label, rows in accounting["records"].items():
        if not rows:
            failures.append(f"Slurm accounting has no record for {label}")
        elif any(row.get("State") != "COMPLETED" for row in rows):
            failures.append(f"Slurm accounting for {label} is not entirely COMPLETED")
        elif label.endswith("_model") and any(
            int(row.get("ElapsedRaw", 0))
            > int(acceptance["maximum_model_wall_seconds_per_segment"])
            for row in rows
        ):
            failures.append(f"Slurm model wall for {label} exceeds the envelope")
        elif "forcing" in label and any(
            int(row.get("ElapsedRaw", 0))
            > int(acceptance["maximum_forcing_wall_seconds_per_record"])
            for row in rows
        ):
            failures.append(f"Slurm forcing wall for {label} exceeds the envelope")

    payload = {
        "schema_version": 1,
        "status": "PASS" if not failures else "FAIL",
        "decision": (
            "QUALIFIED_100M_ENGINEERING_CAPACITY_ONLY"
            if not failures
            else "HOLD_100M_CAPACITY"
        ),
        "plan": str(args.plan.resolve()),
        "event_decision": plan.get("event_decision"),
        "geometry": geometry,
        "segments": segment_results,
        "combined_output_times": combined_times,
        "restart_sizes_bytes": restart_sizes,
        "restart_boundary": boundary,
        "slurm_accounting": accounting,
        "limitations": config["interpretation"],
        "authorization": {
            "100m_engineering_capacity": not failures,
            "100m_scientific_production": False,
            "annual_cycle": False,
            "twenty_year_production": False,
        },
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print("PASS: national 100 m engineering capacity only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
