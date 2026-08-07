#!/usr/bin/env python3
"""Summarize several hicarprep surface plausibility reports without selecting a policy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def summarize(paths: list[Path]) -> dict:
    cases: dict[str, dict] = {}
    for path in paths:
        report = json.loads(path.read_text())
        valid_time = str(report["valid_time"])
        if valid_time in cases:
            raise ValueError(f"duplicate valid time {valid_time}")
        cases[valid_time] = {
            "status": report["status"],
            "source_sha256": report["source_sha256"],
            "static_sha256": report["static_sha256"],
            "hard_failures": report["hard_failures"],
            "warnings": report["warnings"],
            "native_icon_smi_median_by_layer": [
                layer["p50"] for layer in report["native_icon_indices"]["smi_by_layer"]
            ],
            "native_icon_relative_saturation_median_by_layer": [
                layer["p50"]
                for layer in report["native_icon_indices"]["relative_saturation_by_layer"]
            ],
            "methods": {
                method: {
                    "hydraulic_clip_rate": values["hydraulic_clip_rate"],
                    "global_finite_fallback_count": values["global_finite_fallback_count"],
                    "cross_surface_in_stencil_fallback_count": values.get(
                        "cross_surface_in_stencil_fallback_count", 0
                    ),
                    "maximum_fallback_distance_km": values.get(
                        "maximum_fallback_distance_km"
                    ),
                    "fallback_distance_p99_km": values.get("fallback_distance_p99_km"),
                    "static_epoch_back_extrapolation": values.get(
                        "static_epoch_back_extrapolation", "unknown"
                    ),
                    "static_landuse_epoch_valid_from": values.get(
                        "static_landuse_epoch_valid_from", ""
                    ),
                    "vwc_median_by_layer": [layer["p50"] for layer in values["soil_vwc_by_layer"]],
                    "vwc_boundary_jump_p99_by_layer": [
                        values["soil_vwc_neighbor_jumps"][str(layer)][
                            "across_soil_class"
                        ]["p99"]
                        for layer in range(1, 5)
                    ],
                    "transfer_index_boundary_jump_p99_by_layer": [
                        values["transfer_index_neighbor_jumps"][str(layer)][
                            "across_soil_class"
                        ]["p99"]
                        for layer in range(1, 5)
                    ],
                    "product_sha256": values["product_sha256"],
                }
                for method, values in report["methods"].items()
            },
            "pairwise_vwc": report["pairwise_vwc"],
            "report": str(path),
        }
    passed = all(case["status"] == "PASS_INPUT_PLAUSIBILITY" for case in cases.values())
    return {
        "schema": "hicarprep-surface-multicase-summary-v3",
        "status": "PASS_ALL_INPUT_PLAUSIBILITY" if passed else "FAIL_INPUT_PLAUSIBILITY",
        "scientific_policy_selection": "INDETERMINATE",
        "cases": cases,
        "interpretation": (
            "This matrix screens invalid inputs and quantifies method sensitivity. It does not "
            "select SMI or relative saturation without controlled HICAR response evidence."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = summarize(args.report)
    atomic_json(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS_ALL_INPUT_PLAUSIBILITY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
