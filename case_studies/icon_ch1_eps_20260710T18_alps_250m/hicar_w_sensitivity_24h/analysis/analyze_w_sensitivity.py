#!/usr/bin/env python3
"""Analyze HICAR upper-level vertical-wind sensitivity outputs."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date


CASE_ROOT = Path(__file__).resolve().parents[1]
STATIC_FILE = CASE_ROOT.parent / "static" / "domain_static_relaxed.nc"
REPORT = CASE_ROOT / "analysis" / "w_sensitivity_report.md"
SUMMARY_CSV = CASE_ROOT / "analysis" / "w_sensitivity_summary.csv"
LEVEL_CSV = CASE_ROOT / "analysis" / "w_sensitivity_level_maxima.csv"
NEAR_SURFACE_CSV = CASE_ROOT / "analysis" / "w_sensitivity_near_surface_wind.csv"

CASES = [
    ("none_20km", "wind=none, 20 km lid"),
    ("var_no_sx_20km", "variational solver, no Sx, 20 km lid"),
    ("var_sx_20km", "variational solver, Sx, 20 km lid"),
    ("var_sx_top12km", "variational solver, Sx, 12 km lid"),
]

TARGET_AGL = [50.0, 100.0, 200.0]


@dataclass
class CaseData:
    case: str
    label: str
    files: list[Path]
    z: np.ndarray
    agl: np.ndarray
    topo: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    w: np.ndarray
    w_grid: np.ndarray
    density: np.ndarray
    temperature: np.ndarray
    pressure: np.ndarray
    wind_alpha: np.ndarray | None
    speed: np.ndarray
    times: list[str]


def read_static() -> np.ndarray:
    with Dataset(STATIC_FILE) as ds:
        return np.asarray(ds.variables["topo"][:], dtype=np.float64)


def read_case(case: str, label: str, topo: np.ndarray) -> CaseData:
    out_dir = CASE_ROOT / case / "output"
    files = sorted(out_dir.glob("*.nc"))
    if not files:
        raise FileNotFoundError(f"No output files in {out_dir}")

    arrays: dict[str, list[np.ndarray]] = {
        "w": [],
        "w_grid": [],
        "density": [],
        "temperature": [],
        "pressure": [],
        "speed": [],
    }
    alpha_parts: list[np.ndarray] = []
    times: list[str] = []
    z = lat = lon = None

    for file in files:
        with Dataset(file) as ds:
            if z is None:
                z = np.asarray(ds.variables["z"][:], dtype=np.float32)
                lat = np.asarray(ds.variables["lat"][:], dtype=np.float32)
                lon = np.asarray(ds.variables["lon"][:], dtype=np.float32)

            for name in ("w", "w_grid", "density", "temperature", "pressure"):
                arrays[name].append(np.asarray(ds.variables[name][:], dtype=np.float32))

            u = np.asarray(ds.variables["u"][:], dtype=np.float32)
            v = np.asarray(ds.variables["v"][:], dtype=np.float32)
            u_c = 0.5 * (u[:, :, :, :-1] + u[:, :, :, 1:])
            v_c = 0.5 * (v[:, :, :-1, :] + v[:, :, 1:, :])
            arrays["speed"].append(np.sqrt(u_c * u_c + v_c * v_c, dtype=np.float32))

            if "wind_alpha" in ds.variables:
                alpha_parts.append(np.asarray(ds.variables["wind_alpha"][:], dtype=np.float32))

            time_var = ds.variables["time"]
            dates = num2date(time_var[:], units=time_var.units, calendar=getattr(time_var, "calendar", "standard"))
            times.extend(str(d) for d in dates)

    assert z is not None and lat is not None and lon is not None
    agl = z.astype(np.float64) - topo[None, :, :]
    return CaseData(
        case=case,
        label=label,
        files=files,
        z=z,
        agl=agl,
        topo=topo,
        lat=lat,
        lon=lon,
        w=np.concatenate(arrays["w"], axis=0),
        w_grid=np.concatenate(arrays["w_grid"], axis=0),
        density=np.concatenate(arrays["density"], axis=0),
        temperature=np.concatenate(arrays["temperature"], axis=0),
        pressure=np.concatenate(arrays["pressure"], axis=0),
        wind_alpha=np.concatenate(alpha_parts, axis=0) if alpha_parts else None,
        speed=np.concatenate(arrays["speed"], axis=0),
        times=times,
    )


def abs_stats(arr: np.ndarray) -> dict[str, float]:
    aa = np.abs(arr).reshape(-1)
    return {
        "mean": float(np.mean(aa)),
        "p95": float(np.percentile(aa, 95.0)),
        "p99": float(np.percentile(aa, 99.0)),
        "p999": float(np.percentile(aa, 99.9)),
        "max": float(np.max(aa)),
    }


def max_location(arr: np.ndarray, case: CaseData) -> dict[str, object]:
    idx = np.unravel_index(np.argmax(np.abs(arr)), arr.shape)
    t, k, y, x = (int(v) for v in idx)
    return {
        "time_index": t,
        "time": case.times[t] if t < len(case.times) else str(t),
        "level": k,
        "y": y,
        "x": x,
        "value": float(arr[idx]),
        "abs_value": float(abs(arr[idx])),
        "agl_m": float(case.agl[k, y, x]),
        "asl_m": float(case.z[k, y, x]),
        "topo_m": float(case.topo[y, x]),
        "lat": float(case.lat[y, x]),
        "lon": float(case.lon[y, x]),
    }


def cell_argmax_level_counts(arr: np.ndarray) -> dict[int, int]:
    nt, nz, ny, nx = arr.shape
    flat = np.abs(arr).reshape(nt * nz, ny, nx)
    level_idx = (np.argmax(flat, axis=0) % nz).astype(np.int32)
    vals, counts = np.unique(level_idx, return_counts=True)
    return {int(v): int(c) for v, c in zip(vals, counts)}


def nearest_level_indices(agl: np.ndarray, target: float) -> np.ndarray:
    return np.argmin(np.abs(agl - target), axis=0).astype(np.int32)


def gather_by_level(field: np.ndarray, level_idx: np.ndarray) -> np.ndarray:
    nt, _, ny, nx = field.shape
    y_idx = np.arange(ny)[None, :, None]
    x_idx = np.arange(nx)[None, None, :]
    lev = level_idx[None, :, :]
    return field[:, lev, y_idx, x_idx].reshape(nt, ny, nx)


def summarize_case(case: CaseData) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    w_stats = abs_stats(case.w)
    wg_stats = abs_stats(case.w_grid)
    dens_top = case.density[:, -1, :, :]
    upper5 = slice(max(case.w.shape[1] - 5, 0), case.w.shape[1])
    temp_min = float(np.min(case.temperature))
    q = {}
    for prefix, stats in (("w", w_stats), ("w_grid", wg_stats)):
        for key, value in stats.items():
            q[f"{prefix}_{key}"] = value

    w_loc = max_location(case.w, case)
    wg_loc = max_location(case.w_grid, case)
    w_level_counts = cell_argmax_level_counts(case.w)
    dominant_level = max(w_level_counts.items(), key=lambda kv: kv[1])

    row = {
        "case": case.case,
        "label": case.label,
        "nt": case.w.shape[0],
        "nz": case.w.shape[1],
        "z_top_median_m": float(np.median(case.z[-1])),
        "agl_top_median_m": float(np.median(case.agl[-1])),
        "n_median_levels_below_200m_agl": int(np.sum(np.median(case.agl, axis=(1, 2)) <= 200.0)),
        "n_median_levels_below_1000m_agl": int(np.sum(np.median(case.agl, axis=(1, 2)) <= 1000.0)),
        "min_temperature_K": temp_min,
        "top_density_mean_kgm3": float(np.mean(dens_top)),
        "top_level_w_max": float(np.max(np.abs(case.w[:, -1, :, :]))),
        "top_level_w_p99": float(np.percentile(np.abs(case.w[:, -1, :, :]), 99.0)),
        "top_level_w_grid_max": float(np.max(np.abs(case.w_grid[:, -1, :, :]))),
        "top_level_w_grid_p99": float(np.percentile(np.abs(case.w_grid[:, -1, :, :]), 99.0)),
        "upper5_w_max": float(np.max(np.abs(case.w[:, upper5, :, :]))),
        "upper5_w_p99": float(np.percentile(np.abs(case.w[:, upper5, :, :]), 99.0)),
        "upper5_w_grid_max": float(np.max(np.abs(case.w_grid[:, upper5, :, :]))),
        "upper5_w_grid_p99": float(np.percentile(np.abs(case.w_grid[:, upper5, :, :]), 99.0)),
        "w_max_level": w_loc["level"],
        "w_max_time": w_loc["time"],
        "w_max_agl_m": w_loc["agl_m"],
        "w_max_topo_m": w_loc["topo_m"],
        "w_grid_max_level": wg_loc["level"],
        "w_grid_max_time": wg_loc["time"],
        "w_grid_max_agl_m": wg_loc["agl_m"],
        "w_argmax_dominant_level": dominant_level[0],
        "w_argmax_dominant_cell_fraction": dominant_level[1] / (case.w.shape[2] * case.w.shape[3]),
        **q,
    }

    level_rows = []
    for k in range(case.w.shape[1]):
        level_rows.append({
            "case": case.case,
            "level": k,
            "agl_median_m": float(np.median(case.agl[k])),
            "agl_min_m": float(np.min(case.agl[k])),
            "agl_max_m": float(np.max(case.agl[k])),
            "w_abs_max": float(np.max(np.abs(case.w[:, k, :, :]))),
            "w_abs_p99": float(np.percentile(np.abs(case.w[:, k, :, :]), 99.0)),
            "w_grid_abs_max": float(np.max(np.abs(case.w_grid[:, k, :, :]))),
            "w_grid_abs_p99": float(np.percentile(np.abs(case.w_grid[:, k, :, :]), 99.0)),
            "speed_mean": float(np.mean(case.speed[:, k, :, :])),
            "speed_p95": float(np.percentile(case.speed[:, k, :, :], 95.0)),
        })

    surface_rows = []
    for target in TARGET_AGL:
        li = nearest_level_indices(case.agl, target)
        selected_speed = gather_by_level(case.speed, li)
        selected_w = gather_by_level(case.w, li)
        selected_wg = gather_by_level(case.w_grid, li)
        selected_agl = np.take_along_axis(case.agl, li[None, :, :], axis=0)[0]
        vals, counts = np.unique(li, return_counts=True)
        level_mode = int(vals[np.argmax(counts)])
        surface_rows.append({
            "case": case.case,
            "target_agl_m": target,
            "selected_level_mode": level_mode,
            "selected_agl_mean_m": float(np.mean(selected_agl)),
            "selected_agl_min_m": float(np.min(selected_agl)),
            "selected_agl_max_m": float(np.max(selected_agl)),
            "speed_mean": float(np.mean(selected_speed)),
            "speed_p50": float(np.percentile(selected_speed, 50.0)),
            "speed_p95": float(np.percentile(selected_speed, 95.0)),
            "speed_max": float(np.max(selected_speed)),
            "w_abs_p99": float(np.percentile(np.abs(selected_w), 99.0)),
            "w_abs_max": float(np.max(np.abs(selected_w))),
            "w_grid_abs_p99": float(np.percentile(np.abs(selected_wg), 99.0)),
            "w_grid_abs_max": float(np.max(np.abs(selected_wg))),
        })

    return row, level_rows, surface_rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, object]], columns: list[str]) -> str:
    def fmt(v: object) -> str:
        if isinstance(v, float):
            if math.isnan(v):
                return "nan"
            if abs(v) >= 100:
                return f"{v:.1f}"
            if abs(v) >= 10:
                return f"{v:.2f}"
            return f"{v:.3f}"
        return str(v)

    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(fmt(row[c]) for c in columns) + " |")
    return "\n".join(out)


def main() -> None:
    topo = read_static()
    summaries = []
    levels = []
    near_surface = []
    cases: dict[str, CaseData] = {}
    for case_name, label in CASES:
        case = read_case(case_name, label, topo)
        cases[case_name] = case
        row, level_rows, surface_rows = summarize_case(case)
        summaries.append(row)
        levels.extend(level_rows)
        near_surface.extend(surface_rows)

    write_csv(SUMMARY_CSV, summaries)
    write_csv(LEVEL_CSV, levels)
    write_csv(NEAR_SURFACE_CSV, near_surface)

    # Pairwise near-surface speed deltas relative to main candidates.
    delta_rows = []
    refs = ["none_20km", "var_no_sx_20km", "var_sx_20km"]
    for target in TARGET_AGL:
        for ref in refs:
            ref_case = cases[ref]
            ref_li = nearest_level_indices(ref_case.agl, target)
            ref_speed = gather_by_level(ref_case.speed, ref_li)
            for comp_name, comp_case in cases.items():
                if comp_name == ref:
                    continue
                comp_li = nearest_level_indices(comp_case.agl, target)
                comp_speed = gather_by_level(comp_case.speed, comp_li)
                diff = comp_speed - ref_speed
                delta_rows.append({
                    "target_agl_m": target,
                    "reference": ref,
                    "comparison": comp_name,
                    "mean_speed_diff": float(np.mean(diff)),
                    "mean_abs_speed_diff": float(np.mean(np.abs(diff))),
                    "p95_abs_speed_diff": float(np.percentile(np.abs(diff), 95.0)),
                    "max_abs_speed_diff": float(np.max(np.abs(diff))),
                })
    write_csv(CASE_ROOT / "analysis" / "w_sensitivity_near_surface_speed_deltas.csv", delta_rows)

    selected_summary_cols = [
        "case", "nt", "z_top_median_m", "n_median_levels_below_200m_agl",
        "w_max", "w_p99", "w_argmax_dominant_level", "w_argmax_dominant_cell_fraction",
        "w_grid_max", "w_grid_p99", "upper5_w_max", "upper5_w_grid_max",
        "top_density_mean_kgm3", "min_temperature_K",
    ]
    selected_surface_cols = [
        "case", "target_agl_m", "selected_level_mode", "selected_agl_mean_m",
        "speed_mean", "speed_p95", "w_abs_p99", "w_abs_max", "w_grid_abs_p99", "w_grid_abs_max",
    ]
    lines = [
        "# HICAR `w` Sensitivity Result Analysis",
        "",
        "Run window: 25 hourly records from 2026-07-10 18 UTC through 2026-07-11 18 UTC.",
        "",
        "## Core Diagnostics",
        "",
        markdown_table(summaries, selected_summary_cols),
        "",
        "## Wind-Energy Layer Diagnostics",
        "",
        markdown_table(near_surface, selected_surface_cols),
        "",
        "## Main Conclusions",
        "",
    ]

    s_by_case = {r["case"]: r for r in summaries}
    lines.extend([
        f"- `wind = 'none'` keeps the original pathology: its global max `|w|` is `{s_by_case['none_20km']['w_max']:.2f} m s-1`, and the cell-wise max-`|w|` is dominated by level `{s_by_case['none_20km']['w_argmax_dominant_level']}` in `{100*s_by_case['none_20km']['w_argmax_dominant_cell_fraction']:.1f}%` of cells.",
        f"- The variational wind solver sharply reduces physical vertical velocity at 20 km lid height: `var_sx_20km` has global max `|w| = {s_by_case['var_sx_20km']['w_max']:.2f} m s-1` versus `{s_by_case['none_20km']['w_max']:.2f} m s-1` for `none_20km`; `w` p99 falls from `{s_by_case['none_20km']['w_p99']:.2f}` to `{s_by_case['var_sx_20km']['w_p99']:.2f} m s-1`.",
        f"- The 12 km lid does not reduce the global physical-`w` maximum relative to the 20 km variational/Sx case (`{s_by_case['var_sx_top12km']['w_max']:.2f}` versus `{s_by_case['var_sx_20km']['w_max']:.2f} m s-1`), but it does remove the model-top pathology: at the top model level, max `|w|` falls from `{s_by_case['var_sx_20km']['top_level_w_max']:.2f}` to `{s_by_case['var_sx_top12km']['top_level_w_max']:.2f} m s-1`, and top-level max `|w_grid|` falls from `{s_by_case['var_sx_20km']['top_level_w_grid_max']:.2f}` to `{s_by_case['var_sx_top12km']['top_level_w_grid_max']:.2f} m s-1`.",
        "- In the variational runs, the largest residual `w` is no longer at the lid. It occurs around 4-5.5 km AGL over high terrain, so the next tests should distinguish physically plausible mountain-wave/orographic motion from remaining coordinate or solver artifacts.",
        "- Sx has a modest effect on the 20 km-lid run in this small case: it slightly lowers the `w_grid` envelope and near-surface wind speeds, but the difference between `var_no_sx_20km` and `var_sx_20km` is much smaller than the difference between `wind='none'` and the variational solver.",
        "- The current vertical grid remains unsuitable for wind-energy-focused production runs: the 20 km grid has only one median mass level below 200 m AGL, and the 12 km grid also has only one. The future experiment design should therefore prioritize the higher-resolution near-surface vertical grids already proposed.",
        "- The best provisional choice from these four runs is `wind = 'variational solver'`, `Sx = .True.`, and a lower lid near 12 km for suppressing the specific top-level pathology. This is not yet a production setup because the vertical grid is too coarse below 200 m AGL and SLEVE decay is still at the current extreme `1/1` setting rather than a better-tested value such as the HICAR default `2/6`.",
        "",
        "## Files",
        "",
        f"- Summary CSV: `{SUMMARY_CSV}`",
        f"- Level maxima CSV: `{LEVEL_CSV}`",
        f"- Wind-energy layer CSV: `{NEAR_SURFACE_CSV}`",
        f"- Near-surface speed deltas CSV: `{CASE_ROOT / 'analysis' / 'w_sensitivity_near_surface_speed_deltas.csv'}`",
    ])
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
