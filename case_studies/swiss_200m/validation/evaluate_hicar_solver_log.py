#!/usr/bin/env python3
"""Summarize and gate every HICAR wind solve in a completed model log."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import numpy as np


FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
FGMRES = re.compile(
    rf"HICAR native FGMRES\+line: iterations=\s*(\d+)"
    rf"\s+true_residual=\s*({FLOAT})"
    rf"\s+relative_residual=\s*({FLOAT})"
    rf"\s+target=\s*({FLOAT})"
)
STATUS = re.compile(
    r"HICAR BiCGStab status=\s*(-?\d+)\s+iterations=\s*(\d+)"
)
CONSERVATION = re.compile(
    rf"HICAR adjoint conservation: relative_Bq=\s*({FLOAT})"
    rf"\s+target=\s*({FLOAT})"
)
TIME_STEP = re.compile(rf"time_step:\s*({FLOAT})\s+seconds")
SLEVE = re.compile(
    rf"HICAR SLEVE geometry gate: minimum_mass_jacobian=\s*({FLOAT})"
    rf"\s+minimum_interface_thickness=\s*({FLOAT})"
)
RAP = re.compile(
    rf"HICAR (?:multilevel R A P|recursive R A P) verification"
    rf"(?: level \d+)?: host_stencil=\s*({FLOAT})"
    rf"\s+device_halo=\s*({FLOAT})\s+device_stencil=\s*({FLOAT})"
)
RAP_HIERARCHY = re.compile(
    r"HICAR exact Galerkin hierarchy ready: total coarse levels=\s*(\d+)"
)
TERMINAL_GATE = re.compile(
    rf"HICAR terminal collective solve gate:.*?"
    rf"iterations=(\d+)\s+relative_residual=\s*({FLOAT})"
    rf"\s+solution_error=\s*({FLOAT})"
)
TERMINAL_PHYSICAL = re.compile(
    rf"HICAR terminal physical solve: iterations=(\d+)"
    rf"\s+relative_residual=\s*({FLOAT})\s+status=(-?\d+)"
)


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "minimum": float(np.min(array)),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95.0)),
        "maximum": float(np.max(array)),
        "mean": float(np.mean(array)),
    }


def evaluate(text: str, expected_hours: int) -> dict:
    failures: list[str] = []
    solves = [
        {
            "iterations": int(match.group(1)),
            "true_residual": float(match.group(2)),
            "relative_residual": float(match.group(3)),
            "target": float(match.group(4)),
        }
        for match in FGMRES.finditer(text)
    ]
    statuses = [
        {"status": int(match.group(1)), "iterations": int(match.group(2))}
        for match in STATUS.finditer(text)
    ]
    conservation = [
        {"relative": float(match.group(1)), "target": float(match.group(2))}
        for match in CONSERVATION.finditer(text)
    ]
    time_steps = [float(match.group(1)) for match in TIME_STEP.finditer(text)]
    sleve = [
        {
            "minimum_mass_jacobian": float(match.group(1)),
            "minimum_interface_thickness_m": float(match.group(2)),
        }
        for match in SLEVE.finditer(text)
    ]
    rap = [
        tuple(float(match.group(index)) for index in range(1, 4))
        for match in RAP.finditer(text)
    ]
    rap_hierarchy = [int(match.group(1)) for match in RAP_HIERARCHY.finditer(text)]
    terminal_gate = [
        {
            "iterations": int(match.group(1)),
            "relative_residual": float(match.group(2)),
            "solution_error": float(match.group(3)),
        }
        for match in TERMINAL_GATE.finditer(text)
    ]
    terminal_physical = [
        {
            "iterations": int(match.group(1)),
            "relative_residual": float(match.group(2)),
            "status": int(match.group(3)),
        }
        for match in TERMINAL_PHYSICAL.finditer(text)
    ]

    if len(time_steps) != expected_hours:
        failures.append(
            f"parsed {len(time_steps)} completed model hours; expected {expected_hours}"
        )
    if len(solves) < 2 * expected_hours:
        failures.append(
            f"parsed only {len(solves)} FGMRES solves for {expected_hours} hours"
        )
    failed_solves = [
        index
        for index, solve in enumerate(solves)
        if solve["true_residual"] > solve["target"] * (1.0 + 1.0e-12)
        or not np.isfinite(
            [
                solve["true_residual"],
                solve["relative_residual"],
                solve["target"],
            ]
        ).all()
    ]
    if failed_solves:
        failures.append(f"{len(failed_solves)} FGMRES solves exceeded their target")
    if len(statuses) != len(solves):
        failures.append(
            f"parsed {len(statuses)} solver statuses for {len(solves)} solves"
        )
    nonzero_statuses = [
        index for index, status in enumerate(statuses) if status["status"] != 0
    ]
    if nonzero_statuses:
        failures.append(f"{len(nonzero_statuses)} solver statuses are nonzero")
    if len(conservation) < expected_hours:
        failures.append(
            f"parsed only {len(conservation)} conservation gates for "
            f"{expected_hours} hours"
        )
    failed_conservation = [
        index
        for index, value in enumerate(conservation)
        if value["relative"] > value["target"] * (1.0 + 1.0e-12)
    ]
    if failed_conservation:
        failures.append(
            f"{len(failed_conservation)} adjoint-conservation gates exceeded target"
        )
    if len(sleve) != 1:
        failures.append(f"parsed {len(sleve)} SLEVE geometry gates; expected 1")
    elif (
        sleve[0]["minimum_mass_jacobian"] <= 0.0
        or sleve[0]["minimum_interface_thickness_m"] <= 0.0
    ):
        failures.append("SLEVE geometry gate is not positive")
    declared_rap_levels = rap_hierarchy[0] if len(rap_hierarchy) == 1 else None
    if len(rap_hierarchy) != 1:
        failures.append(
            "parsed "
            f"{len(rap_hierarchy)} exact-Galerkin hierarchy declarations; expected 1"
        )
    elif declared_rap_levels <= 0:
        failures.append("exact-Galerkin hierarchy declared no coarse levels")
    elif len(rap) != declared_rap_levels:
        failures.append(
            f"parsed {len(rap)} exact-RAP verification levels; "
            f"hierarchy declared {declared_rap_levels}"
        )
    if rap and max(abs(value) for record in rap for value in record) > 1.0e-12:
        failures.append("an exact-RAP verification error exceeds 1e-12")
    if len(terminal_gate) != 1:
        failures.append(
            f"parsed {len(terminal_gate)} terminal collective gates; expected 1"
        )
    elif terminal_gate[0]["relative_residual"] > 1.0e-10:
        failures.append("terminal collective residual exceeds 1e-10")
    if len(terminal_physical) != 1:
        failures.append(
            f"parsed {len(terminal_physical)} terminal physical solves; expected 1"
        )
    elif (
        terminal_physical[0]["status"] != 0
        or terminal_physical[0]["relative_residual"] > 1.0e-8
    ):
        failures.append("terminal physical solve did not meet its gate")
    for marker in ("Simulation completed successfully!", "Timing across all compute images:"):
        if marker not in text:
            failures.append(f"model log lacks completion marker: {marker}")

    relative_ratios = [
        solve["true_residual"] / solve["target"]
        for solve in solves
        if solve["target"] > 0.0
    ]
    return {
        "schema_version": 1,
        "status": "FAIL" if failures else "PASS",
        "expected_model_hours": expected_hours,
        "solver": {
            "solve_count": len(solves),
            "iterations": distribution(
                [float(solve["iterations"]) for solve in solves]
            ),
            "relative_residual": distribution(
                [solve["relative_residual"] for solve in solves]
            ),
            "true_residual_to_target_ratio": distribution(relative_ratios),
            "failed_target_count": len(failed_solves),
            "status_record_count": len(statuses),
            "nonzero_status_count": len(nonzero_statuses),
        },
        "adjoint_conservation": {
            "gate_count": len(conservation),
            "relative_Bq": distribution(
                [value["relative"] for value in conservation]
            ),
            "relative_to_target": distribution(
                [
                    value["relative"] / value["target"]
                    for value in conservation
                    if value["target"] > 0.0
                ]
            ),
            "failed_target_count": len(failed_conservation),
        },
        "model_hour_wall_time_seconds": distribution(time_steps),
        "sleve_geometry": sleve,
        "exact_rap": {
            "level_count": len(rap),
            "declared_level_count": declared_rap_levels,
            "hierarchy_declaration_count": len(rap_hierarchy),
            "maximum_absolute_error": max(
                (abs(value) for record in rap for value in record),
                default=None,
            ),
        },
        "terminal_collective_gate": terminal_gate,
        "terminal_physical_solve": terminal_physical,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-log", type=Path, required=True)
    parser.add_argument("--expected-hours", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.model_log.read_text(errors="replace"), args.expected_hours)
    result["model_log"] = str(args.model_log.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=args.report.parent, delete=False
    ) as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, args.report)
    if result["status"] != "PASS":
        for failure in result["failures"]:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print(
        f"PASS: {result['solver']['solve_count']} solves and "
        f"{result['adjoint_conservation']['gate_count']} conservation gates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
