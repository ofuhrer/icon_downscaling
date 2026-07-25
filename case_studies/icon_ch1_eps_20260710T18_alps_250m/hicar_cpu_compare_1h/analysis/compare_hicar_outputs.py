#!/usr/bin/env python3
"""Compare one-hour HICAR debug and optimized CPU outputs."""

import argparse
import glob
import math
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import netCDF4
import numpy as np


DEFAULT_TOLERANCES = {
    "temperature": 1.0e-1,
    "qv": 5.0e-5,
    "u": 1.0e-2,
    "v": 1.0e-2,
    "w": 1.0e-1,
    "precipitation": 1.0e-4,
}


def expand_patterns(patterns: Iterable[str]) -> List[Path]:
    paths = []  # type: List[Path]
    for pattern in patterns:
        matches = sorted(Path(p) for p in glob.glob(pattern))
        if not matches:
            raise SystemExit(f"No files matched pattern: {pattern}")
        paths.extend(matches)
    return sorted(paths)


def read_variable(path: Path, name: str) -> np.ndarray:
    with netCDF4.Dataset(path) as ds:
        if name not in ds.variables:
            raise KeyError(f"{path}: missing variable {name}")
        data = ds.variables[name][:]
    return np.asarray(np.ma.filled(data, np.nan), dtype=np.float64)


def finite_min_max(arr: np.ndarray) -> Tuple[float, float]:
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return math.nan, math.nan
    return float(np.min(finite)), float(np.max(finite))


def compare_array(reference: np.ndarray, candidate: np.ndarray) -> Dict[str, object]:
    if reference.shape != candidate.shape:
        return {"shape_match": False}

    ref_finite = np.isfinite(reference)
    cand_finite = np.isfinite(candidate)
    finite_mask = ref_finite & cand_finite
    diff = candidate - reference
    finite_diff = diff[finite_mask]

    if finite_diff.size == 0:
        max_abs = math.nan
        mean_abs = math.nan
        rmse = math.nan
        max_rel = math.nan
        p99_abs = math.nan
        p999_abs = math.nan
    else:
        abs_diff = np.abs(finite_diff)
        denom = np.maximum(np.abs(reference[finite_mask]), 1.0e-12)
        max_abs = float(np.max(abs_diff))
        mean_abs = float(np.mean(abs_diff))
        rmse = float(np.sqrt(np.mean(finite_diff * finite_diff)))
        max_rel = float(np.max(abs_diff / denom))
        p99_abs = float(np.percentile(abs_diff, 99.0))
        p999_abs = float(np.percentile(abs_diff, 99.9))

    return {
        "shape_match": True,
        "ref_nonfinite": int(reference.size - np.count_nonzero(ref_finite)),
        "cand_nonfinite": int(candidate.size - np.count_nonzero(cand_finite)),
        "max_abs": max_abs,
        "mean_abs": mean_abs,
        "rmse": rmse,
        "max_rel": max_rel,
        "p99_abs": p99_abs,
        "p999_abs": p999_abs,
    }


def parse_tolerance(values: List[str]) -> Dict[str, float]:
    tolerances = dict(DEFAULT_TOLERANCES)
    for item in values:
        if "=" not in item:
            raise SystemExit(f"Invalid tolerance '{item}', expected name=value")
        name, value = item.split("=", 1)
        tolerances[name] = float(value)
    return tolerances


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", nargs="+", required=True, help="Debug output glob(s)")
    parser.add_argument("--release", nargs="+", required=True, help="Release output glob(s)")
    parser.add_argument("--out", required=True, help="Text report path")
    parser.add_argument(
        "--var",
        action="append",
        dest="vars",
        default=[],
        help="Variable to compare; defaults to key HICAR output variables",
    )
    parser.add_argument(
        "--tolerance",
        action="append",
        default=[],
        help="Override max-absolute-difference tolerance as name=value",
    )
    args = parser.parse_args()

    debug_files = expand_patterns(args.debug)
    release_files = expand_patterns(args.release)
    variables = args.vars or list(DEFAULT_TOLERANCES)
    tolerances = parse_tolerance(args.tolerance)

    lines = [
        "HICAR CPU Debug vs Release Comparison",
        "======================================",
        "",
        f"Debug files: {len(debug_files)}",
        f"Release files: {len(release_files)}",
        "",
    ]

    failed = False
    if len(debug_files) != len(release_files):
        failed = True
        lines.append(f"FAIL: file-count mismatch: debug={len(debug_files)} release={len(release_files)}")

    for debug_path, release_path in zip(debug_files, release_files):
        lines.extend(
            [
                f"File pair: {debug_path.name} vs {release_path.name}",
                "-" * (22 + len(debug_path.name) + len(release_path.name)),
            ]
        )

        try:
            debug_time = read_variable(debug_path, "time")
            release_time = read_variable(release_path, "time")
            time_metrics = compare_array(debug_time, release_time)
            if not time_metrics.get("shape_match") or time_metrics.get("max_abs", math.inf) != 0.0:
                failed = True
                lines.append(f"FAIL time: {time_metrics}")
            else:
                lines.append("PASS time: exact coordinate match")
        except KeyError as exc:
            failed = True
            lines.append(f"FAIL time: {exc}")

        for name in variables:
            try:
                reference = read_variable(debug_path, name)
                candidate = read_variable(release_path, name)
            except KeyError as exc:
                failed = True
                lines.append(f"FAIL {name}: {exc}")
                continue

            metrics = compare_array(reference, candidate)
            if not metrics.get("shape_match"):
                failed = True
                lines.append(f"FAIL {name}: shape mismatch {reference.shape} vs {candidate.shape}")
                continue

            ref_min, ref_max = finite_min_max(reference)
            cand_min, cand_max = finite_min_max(candidate)
            tolerance = tolerances.get(name, math.inf)
            status = "PASS"
            if metrics["ref_nonfinite"] or metrics["cand_nonfinite"]:
                status = "FAIL"
            elif metrics["max_abs"] > tolerance:
                status = "FAIL"

            if status == "FAIL":
                failed = True

            lines.append(
                f"{status} {name}: "
                f"shape={reference.shape}, "
                f"ref_range=[{ref_min:.8g}, {ref_max:.8g}], "
                f"rel_range=[{cand_min:.8g}, {cand_max:.8g}], "
                f"max_abs={metrics['max_abs']:.8g}, "
                f"p99_abs={metrics['p99_abs']:.8g}, "
                f"p999_abs={metrics['p999_abs']:.8g}, "
                f"mean_abs={metrics['mean_abs']:.8g}, "
                f"rmse={metrics['rmse']:.8g}, "
                f"max_rel={metrics['max_rel']:.8g}, "
                f"tol={tolerance:.8g}, "
                f"nonfinite=({metrics['ref_nonfinite']}, {metrics['cand_nonfinite']})"
            )
        lines.append("")

    lines.append("Overall status: " + ("FAIL" if failed else "PASS"))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_path)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
