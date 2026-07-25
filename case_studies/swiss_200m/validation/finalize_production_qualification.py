#!/usr/bin/env python3
"""Publish the measured six-hour Swiss production-qualification manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rss_kib(value: str) -> int:
    if not value:
        return 0
    match = re.fullmatch(r"([0-9.]+)([KMGT]?)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"unrecognized Slurm RSS value: {value}")
    number = float(match.group(1))
    unit = match.group(2).upper()
    multiplier = {"": 1.0 / 1024.0, "K": 1.0, "M": 1024.0, "G": 1024.0**2, "T": 1024.0**3}
    return int(round(number * multiplier[unit]))


def slurm_accounting(job_id: int) -> dict:
    result = subprocess.run(
        [
            "sacct",
            "-P",
            "-n",
            "-j",
            str(job_id),
            "--format=JobIDRaw,State,ElapsedRaw,MaxRSS,ExitCode",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    records = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        job, state, elapsed, rss, exit_code, *_ = line.split("|")
        records.append(
            {
                "job": job,
                "state": state.split()[0],
                "elapsed_seconds": int(elapsed or 0),
                "max_rss_kib": rss_kib(rss),
                "exit_code": exit_code,
            }
        )
    root = next((record for record in records if record["job"] == str(job_id)), None)
    if root is None:
        raise SystemExit(f"sacct has no root record for job {job_id}")
    return {
        "job_id": job_id,
        "state": root["state"],
        "elapsed_seconds": root["elapsed_seconds"],
        "peak_step_rss_kib": max(record["max_rss_kib"] for record in records),
        "records": records,
    }


def last_float(pattern: str, text: str, label: str) -> float:
    matches = re.findall(pattern, text)
    if not matches:
        raise SystemExit(f"model log lacks {label}")
    return float(matches[-1])


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--model-job", type=int, required=True)
    parser.add_argument("--output-job", type=int, required=True)
    parser.add_argument("--source-job", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    run = args.run.resolve()
    paths = {
        "log": run / "model.out",
        "namelist": run / "input.nml",
        "output_report": run / "output_validation.json",
        "source_report": run / "source_aware_validation.json",
        "forcing_manifest": run / "forcing_manifest.json",
        "plan": run / "production_plan.json",
        "source_commit": run / "source_commit.txt",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise SystemExit(f"missing {name}: {path}")
    output_report = json.loads(paths["output_report"].read_text())
    source_report = json.loads(paths["source_report"].read_text())
    forcing_manifest = json.loads(paths["forcing_manifest"].read_text())
    plan = json.loads(paths["plan"].read_text())
    log = paths["log"].read_text(errors="replace")
    output_entry = output_report["files"][0]

    model_accounting = slurm_accounting(args.model_job)
    output_accounting = slurm_accounting(args.output_job)
    source_accounting = slurm_accounting(args.source_job)

    residuals = [
        float(value)
        for value in re.findall(r"relative_residual=\s*([0-9.Ee+-]+)", log)
    ]
    constraints = [
        float(value)
        for value in re.findall(r"relative_Bq=\s*([0-9.Ee+-]+)", log)
    ]
    if not residuals or not constraints:
        raise SystemExit("model log lacks solver residual or conservation records")
    model_total_seconds = last_float(r"Total time:\s*([0-9.Ee+-]+)", log, "total timing")
    minimum_jacobian = last_float(
        r"minimum_mass_jacobian=\s*([0-9.Ee+-]+)", log, "minimum mass Jacobian"
    )
    minimum_thickness = last_float(
        r"minimum_interface_thickness=\s*([0-9.Ee+-]+)",
        log,
        "minimum interface thickness",
    )

    budgets = plan["budgets"]
    acceptance = plan["acceptance"]
    failures = []
    required_states = {
        "model": model_accounting["state"],
        "output_validation": output_accounting["state"],
        "source_validation": source_accounting["state"],
    }
    failures.extend(
        f"{name} job state is {state}"
        for name, state in required_states.items()
        if state != "COMPLETED"
    )
    if output_report.get("status") != "PASS":
        failures.append("bounded-memory output validation did not pass")
    if source_report.get("status") != "PASS":
        failures.append("source-aware physical comparison did not pass")
    if forcing_manifest.get("status") != "PASS":
        failures.append("forcing-series manifest did not pass")
    if model_accounting["elapsed_seconds"] > budgets["acceptance_wall_seconds"]:
        failures.append("model wall time exceeds the accepted budget")
    peak_rss_gib = model_accounting["peak_step_rss_kib"] / 1024.0**2
    if peak_rss_gib > budgets["maximum_task_rss_gib"]:
        failures.append("peak model task RSS exceeds the accepted budget")
    output_gib = output_entry["size_bytes"] / 1024.0**3
    if output_gib > budgets["maximum_output_gib"]:
        failures.append("two-record output exceeds the accepted storage budget")
    if minimum_jacobian < acceptance["minimum_mass_jacobian"]:
        failures.append("minimum mass Jacobian fails the geometry gate")
    if minimum_thickness < acceptance["minimum_interface_thickness_m"]:
        failures.append("minimum interface thickness fails the geometry gate")
    if max(residuals) > acceptance["maximum_solver_relative_residual"] * (1.0 + 1.0e-10):
        failures.append("a reported converged solver residual exceeds its gate")
    if max(constraints) > acceptance["maximum_independent_constraint_ratio"]:
        failures.append("an independent mass-constraint ratio exceeds its gate")
    if "Simulation completed successfully!" not in log:
        failures.append("model completion marker is absent")

    actual_100m_wall = int(math.ceil(2.0 * model_accounting["elapsed_seconds"] / 300.0) * 300)
    actual_100m_output_gib = 4.0 * output_gib
    payload = {
        "status": "PASS" if not failures else "FAIL",
        "scope": (
            "ICON REA-L-CH1 to HICAR, Switzerland 200 m, "
            "2010-01-01 00--06 UTC production-candidate pilot"
        ),
        "failures": failures,
        "configuration": plan["hicar"],
        "forcing": {
            "manifest_status": forcing_manifest.get("status"),
            "records": forcing_manifest.get("records"),
            "start": forcing_manifest.get("start"),
            "end": forcing_manifest.get("end"),
            "manifest_sha256": sha256(paths["forcing_manifest"]),
        },
        "model": {
            **model_accounting,
            "reported_total_seconds": model_total_seconds,
            "source_commit": paths["source_commit"].read_text().strip(),
            "peak_step_rss_gib": peak_rss_gib,
        },
        "geometry": {
            "minimum_mass_jacobian": minimum_jacobian,
            "minimum_interface_thickness_m": minimum_thickness,
        },
        "solver": {
            "maximum_reported_relative_residual": max(residuals),
            "maximum_independent_constraint_ratio": max(constraints),
            "relative_residual_gate": acceptance["maximum_solver_relative_residual"],
            "constraint_ratio_gate": acceptance["maximum_independent_constraint_ratio"],
        },
        "output": {
            "validation_job": output_accounting,
            "validation_status": output_report.get("status"),
            "records": output_entry["dimensions"]["time"],
            "size_bytes": output_entry["size_bytes"],
            "size_gib": output_gib,
            "sha256": output_entry["sha256"],
            "ranges": {
                name: {
                    "minimum": details["minimum"],
                    "maximum": details["maximum"],
                    "nonfinite": details["nonfinite"],
                }
                for name, details in output_entry["variables"].items()
            },
            "minimum_vertical_spacing_m": output_entry["z_vertical_difference"]["minimum"],
            "maximum_pressure_vertical_difference_pa": output_entry[
                "pressure_vertical_difference"
            ]["maximum"],
        },
        "source_aware_comparison": {
            "validation_job": source_accounting,
            "status": source_report.get("status"),
            "method": source_report.get("method"),
            "endpoints": source_report.get("endpoints"),
        },
        "artifacts": {
            "run_directory": str(run),
            "model_log_sha256": sha256(paths["log"]),
            "namelist_sha256": sha256(paths["namelist"]),
            "output_validation_sha256": sha256(paths["output_report"]),
            "source_validation_sha256": sha256(paths["source_report"]),
        },
        "measured_100m_decision_path": {
            "status": "ESTIMATE_ONLY",
            "recommended_nodes": 16,
            "compute_gpus": 64,
            "estimated_six_hour_wall_seconds_rounded_up": actual_100m_wall,
            "recommended_wall_request_seconds": max(7200, 2 * actual_100m_wall),
            "estimated_two_record_output_gib": actual_100m_output_gib,
            "derivation": (
                "four times the horizontal cells, half the stable timestep, "
                "and four times the GPU count: approximately twice the measured wall "
                "time and four times the measured output"
            ),
            "required_before_submission": [
                "construct and validate the actual 100 m SLEVE geometry",
                "pass the 100 m initial adjoint solve and conservation gates",
                "confirm per-node and aggregate memory with a bounded 100 m capacity run",
            ],
        },
        "limitations": [
            "Six hours establishes a production-candidate engineering baseline, not climatological skill.",
            "The comparison is against the driving REA-L atmospheric state, not independent observations.",
            "The source-aware comparison uses sparse nearest horizontal columns and height interpolation.",
            "The 100 m resource and stability figures remain estimates until an actual 100 m capacity run.",
        ],
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(f"PASS: production qualification published at {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
