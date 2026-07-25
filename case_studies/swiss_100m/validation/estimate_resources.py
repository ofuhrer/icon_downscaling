#!/usr/bin/env python3
"""Recompute the resource-plan output volumes from the national-domain plan."""
from __future__ import annotations

import json
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
DOMAIN = json.loads((CASE / "config" / "domain.json").read_text())
RESOURCE = json.loads((CASE / "config" / "resource_plan.json").read_text())
GIB = 1024 ** 3
TIB = 1024 ** 4


def estimate(section: dict[str, object]) -> tuple[float, float]:
    cells = DOMAIN["horizontal_cells"]
    levels = RESOURCE["vertical_levels"]
    bytes_per_output = 4 * cells * (section["two_d_float32_fields"] + levels * section["three_d_float32_fields"])
    outputs_per_year = 365.25 * 86400 / section["interval_seconds"]
    return bytes_per_output / GIB, bytes_per_output * outputs_per_year / TIB


def main() -> int:
    errors: list[str] = []
    for name in ("output_default", "output_validation_only"):
        actual_gib, actual_tib = estimate(RESOURCE[name])
        expected_gib = RESOURCE[name]["estimated_uncompressed_gib_per_output"]
        expected_tib = RESOURCE[name]["estimated_uncompressed_tib_per_year"]
        if abs(actual_gib - expected_gib) > 0.01 or abs(actual_tib - expected_tib) > 0.05:
            errors.append(f"{name}: config values do not match calculated output volume")
    expected_per_gpu = DOMAIN["horizontal_cells"] // RESOURCE["compute_gpus"]
    if abs(expected_per_gpu - RESOURCE["target_horizontal_cells_per_gpu"]) > 1:
        errors.append("target_horizontal_cells_per_gpu does not match domain / compute_gpus")
    if errors:
        raise SystemExit("\n".join(errors))
    print(json.dumps({
        "cells": DOMAIN["horizontal_cells"],
        "compute_gpus": RESOURCE["compute_gpus"],
        "cells_per_gpu": expected_per_gpu,
        "default_output": dict(zip(("gib_per_output", "tib_per_year"), estimate(RESOURCE["output_default"]))),
        "validation_output": dict(zip(("gib_per_output", "tib_per_year"), estimate(RESOURCE["output_validation_only"])))
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
