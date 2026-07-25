#!/usr/bin/env python3
"""Compare HICAR's online fixed-height diagnostics with the reference method."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import netCDF4
import numpy as np

from derive_hicar_wind_climatology import (
    DEFAULT_HEIGHTS_M,
    _as_float64,
    _read_mass_winds,
    _read_z_block,
    interpolate_columns,
)


ONLINE_VARIABLES = {
    "u_agl": "eastward wind",
    "v_agl": "northward wind",
    "rho_agl": "air density",
}


def _difference_summary(
    actual: np.ndarray,
    expected: np.ndarray,
) -> dict[str, float | int]:
    actual = _as_float64(actual)
    expected = _as_float64(expected)
    finite = np.isfinite(actual) & np.isfinite(expected)
    nonfinite = int(actual.size - np.count_nonzero(finite))
    if not np.any(finite):
        return {
            "maximum_absolute_error": float("inf"),
            "root_mean_square_error": float("inf"),
            "nonfinite_pairs": nonfinite,
        }
    difference = actual[finite] - expected[finite]
    return {
        "maximum_absolute_error": float(np.max(np.abs(difference))),
        "root_mean_square_error": float(np.sqrt(np.mean(difference**2))),
        "nonfinite_pairs": nonfinite,
    }


def validate_file(
    input_path: Path,
    static_path: Path,
    *,
    absolute_tolerance: float = 2.0e-5,
    y_block_size: int = 128,
) -> dict[str, object]:
    """Validate online values against de-staggering and AGL interpolation."""
    input_path = input_path.resolve()
    static_path = static_path.resolve()
    failures: list[str] = []
    summaries = {
        name: {
            "maximum_absolute_error": 0.0,
            "root_sum_square_error": 0.0,
            "finite_count": 0,
            "nonfinite_pairs": 0,
        }
        for name in ONLINE_VARIABLES
    }

    if absolute_tolerance <= 0.0:
        raise ValueError("absolute_tolerance must be positive")
    if y_block_size <= 0:
        raise ValueError("y_block_size must be positive")

    with (
        netCDF4.Dataset(input_path) as source,
        netCDF4.Dataset(static_path) as static,
    ):
        for name in ("time", "level", "lat_y", "lon_x"):
            if name not in source.dimensions:
                failures.append(f"missing dimension {name}")
        for name in ("z", "density", "height_agl", *ONLINE_VARIABLES):
            if name not in source.variables:
                failures.append(f"missing variable {name}")
        if "topo" not in static.variables:
            failures.append("static domain is missing topo")
        if failures:
            return {
                "status": "FAIL",
                "failures": failures,
                "variables": summaries,
            }

        nt = len(source.dimensions["time"])
        nz = len(source.dimensions["level"])
        ny = len(source.dimensions["lat_y"])
        nx = len(source.dimensions["lon_x"])
        heights = tuple(float(value) for value in _as_float64(source["height_agl"][:]))
        if not np.allclose(
            heights,
            DEFAULT_HEIGHTS_M,
            rtol=0.0,
            atol=1.0e-6,
        ):
            failures.append(
                f"height_agl is {list(heights)}, expected {list(DEFAULT_HEIGHTS_M)}"
            )
        expected_shape = (nt, len(heights), ny, nx)
        for name in ONLINE_VARIABLES:
            if source[name].shape != expected_shape:
                failures.append(
                    f"{name} has shape {source[name].shape}, expected {expected_shape}"
                )
        if failures:
            return {
                "status": "FAIL",
                "failures": failures,
                "variables": summaries,
            }

        topo = _as_float64(static["topo"][:])
        if topo.shape != (ny, nx):
            failures.append(f"topo has shape {topo.shape}, expected {(ny, nx)}")
        else:
            for time_index in range(nt):
                for y_start in range(0, ny, y_block_size):
                    y_stop = min(y_start + y_block_size, ny)
                    y_slice = slice(y_start, y_stop)
                    z = _read_z_block(
                        source["z"],
                        time_index,
                        y_slice,
                        nt,
                        nz,
                        ny,
                        nx,
                    )
                    z_agl = z - topo[y_slice, :][None, :, :]
                    u_mass, v_mass, _ = _read_mass_winds(
                        source,
                        time_index,
                        y_slice,
                        nz,
                        y_stop - y_start,
                        nx,
                    )
                    density = _as_float64(source["density"][time_index, :, y_slice, :])
                    expected = {
                        "u_agl": interpolate_columns(
                            z_agl,
                            u_mass,
                            heights,
                            field_name="eastward wind",
                        ),
                        "v_agl": interpolate_columns(
                            z_agl,
                            v_mass,
                            heights,
                            field_name="northward wind",
                        ),
                        "rho_agl": interpolate_columns(
                            z_agl,
                            density,
                            heights,
                            field_name="air density",
                        ),
                    }
                    for name in ONLINE_VARIABLES:
                        actual = source[name][time_index, :, y_slice, :]
                        block = _difference_summary(actual, expected[name])
                        summary = summaries[name]
                        summary["maximum_absolute_error"] = max(
                            summary["maximum_absolute_error"],
                            block["maximum_absolute_error"],
                        )
                        count = int(np.prod(expected[name].shape)) - block["nonfinite_pairs"]
                        summary["root_sum_square_error"] += (
                            block["root_mean_square_error"] ** 2 * count
                        )
                        summary["finite_count"] += count
                        summary["nonfinite_pairs"] += block["nonfinite_pairs"]

    final_summaries: dict[str, dict[str, float | int]] = {}
    for name, summary in summaries.items():
        finite_count = summary["finite_count"]
        rms = (
            float(np.sqrt(summary["root_sum_square_error"] / finite_count))
            if finite_count
            else float("inf")
        )
        final_summaries[name] = {
            "maximum_absolute_error": summary["maximum_absolute_error"],
            "root_mean_square_error": rms,
            "nonfinite_pairs": summary["nonfinite_pairs"],
        }
        if summary["nonfinite_pairs"]:
            failures.append(
                f"{name} has {summary['nonfinite_pairs']} non-finite comparison pairs"
            )
        if summary["maximum_absolute_error"] > absolute_tolerance:
            failures.append(
                f"{name} maximum absolute error "
                f"{summary['maximum_absolute_error']:.8g} exceeds "
                f"{absolute_tolerance:.8g}"
            )

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "absolute_tolerance": absolute_tolerance,
        "input": str(input_path),
        "static_domain": str(static_path),
        "heights_agl_m": list(DEFAULT_HEIGHTS_M),
        "variables": final_summaries,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--static-domain", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--absolute-tolerance", type=float, default=2.0e-5)
    parser.add_argument("--y-block-size", type=int, default=128)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = validate_file(
            args.input,
            args.static_domain,
            absolute_tolerance=args.absolute_tolerance,
            y_block_size=args.y_block_size,
        )
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    report["created_utc"] = datetime.now(timezone.utc).isoformat()
    if args.report is not None:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_name(f".{report_path.name}.tmp-{os.getpid()}")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, report_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
