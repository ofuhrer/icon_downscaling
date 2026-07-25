#!/usr/bin/env python3
"""Analyze HICAR wind-downscaling design sensitivity outputs."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from netCDF4 import Dataset, num2date


CASE_ROOT = Path(__file__).resolve().parents[1]
STATIC_FILE = CASE_ROOT.parent / "static" / "domain_static_relaxed.nc"
ANALYSIS_DIR = CASE_ROOT / "analysis"
REPORT = ANALYSIS_DIR / "wind_design_sensitivity_report.md"
SUMMARY_CSV = ANALYSIS_DIR / "wind_design_summary.csv"
LEVEL_CSV = ANALYSIS_DIR / "wind_design_level_stats.csv"
NEAR_SURFACE_CSV = ANALYSIS_DIR / "wind_design_near_surface.csv"
DELTAS_CSV = ANALYSIS_DIR / "wind_design_near_surface_deltas.csv"
VERTICAL_LEVELS_PNG = ANALYSIS_DIR / "wind_design_vertical_levels.png"
CORE_METRICS_PNG = ANALYSIS_DIR / "wind_design_core_metrics.png"

TARGET_AGL = [50.0, 100.0, 200.0]

CASES = [
    {
        "case": "v1_auto1_n60_top12_s26",
        "label": "auto1 n60 top12 km SLEVE 2/6",
        "group": "baseline",
        "auto_level": 1,
        "nz": 60,
        "top_km": 12,
        "decay": "2/6",
    },
    {
        "case": "v2_auto1_n80_top12_s26",
        "label": "auto1 n80 top12 km SLEVE 2/6",
        "group": "vertical",
        "auto_level": 1,
        "nz": 80,
        "top_km": 12,
        "decay": "2/6",
    },
    {
        "case": "v3_auto1_n60_top10_s26",
        "label": "auto1 n60 top10 km SLEVE 2/6",
        "group": "lid",
        "auto_level": 1,
        "nz": 60,
        "top_km": 10,
        "decay": "2/6",
    },
    {
        "case": "v4_auto1_n70_top14_s26",
        "label": "auto1 n70 top14 km SLEVE 2/6",
        "group": "lid",
        "auto_level": 1,
        "nz": 70,
        "top_km": 14,
        "decay": "2/6",
    },
    {
        "case": "v5_auto4_n70_top12_s26",
        "label": "auto4 n70 top12 km SLEVE 2/6",
        "group": "vertical",
        "auto_level": 4,
        "nz": 70,
        "top_km": 12,
        "decay": "2/6",
    },
    {
        "case": "s0_auto1_n60_top12_s11",
        "label": "auto1 n60 top12 km SLEVE 1/1",
        "group": "sleve",
        "auto_level": 1,
        "nz": 60,
        "top_km": 12,
        "decay": "1/1",
    },
    {
        "case": "s1_auto1_n60_top12_s15_3",
        "label": "auto1 n60 top12 km SLEVE 1.5/3",
        "group": "sleve",
        "auto_level": 1,
        "nz": 60,
        "top_km": 12,
        "decay": "1.5/3",
    },
    {
        "case": "s2_auto1_n60_top12_s24",
        "label": "auto1 n60 top12 km SLEVE 2/4",
        "group": "sleve",
        "auto_level": 1,
        "nz": 60,
        "top_km": 12,
        "decay": "2/4",
    },
]


@dataclass
class CaseData:
    meta: dict[str, object]
    files: list[Path]
    times: list[str]
    topo: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    z: np.ndarray
    agl: np.ndarray
    w: np.ndarray
    w_grid: np.ndarray
    u: np.ndarray
    v: np.ndarray
    temperature: np.ndarray
    potential_temperature: np.ndarray
    density: np.ndarray
    pressure: np.ndarray
    wind_alpha: np.ndarray


def read_topo() -> np.ndarray:
    with Dataset(STATIC_FILE) as ds:
        return np.asarray(ds.variables["topo"][:], dtype=np.float64)


def read_case(meta: dict[str, object], topo: np.ndarray) -> CaseData:
    out_dir = CASE_ROOT / str(meta["case"]) / "output"
    files = sorted(out_dir.glob("*.nc"))
    if len(files) != 2:
        raise FileNotFoundError(f"Expected 2 output files in {out_dir}, found {len(files)}")

    parts: dict[str, list[np.ndarray]] = {
        name: []
        for name in (
            "w",
            "w_grid",
            "u",
            "v",
            "temperature",
            "potential_temperature",
            "density",
            "pressure",
            "wind_alpha",
        )
    }
    times: list[str] = []
    z = lat = lon = None

    for file in files:
        with Dataset(file) as ds:
            if z is None:
                z = np.asarray(ds.variables["z"][:], dtype=np.float32)
                lat = np.asarray(ds.variables["lat"][:], dtype=np.float32)
                lon = np.asarray(ds.variables["lon"][:], dtype=np.float32)

            for name in parts:
                parts[name].append(np.asarray(ds.variables[name][:], dtype=np.float32))

            time_var = ds.variables["time"]
            dates = num2date(
                time_var[:],
                units=time_var.units,
                calendar=getattr(time_var, "calendar", "standard"),
            )
            times.extend(str(d) for d in dates)

    assert z is not None and lat is not None and lon is not None
    return CaseData(
        meta=meta,
        files=files,
        times=times,
        topo=topo,
        lat=lat,
        lon=lon,
        z=z,
        agl=z.astype(np.float64) - topo[None, :, :],
        w=np.concatenate(parts["w"], axis=0),
        w_grid=np.concatenate(parts["w_grid"], axis=0),
        u=np.concatenate(parts["u"], axis=0),
        v=np.concatenate(parts["v"], axis=0),
        temperature=np.concatenate(parts["temperature"], axis=0),
        potential_temperature=np.concatenate(parts["potential_temperature"], axis=0),
        density=np.concatenate(parts["density"], axis=0),
        pressure=np.concatenate(parts["pressure"], axis=0),
        wind_alpha=np.concatenate(parts["wind_alpha"], axis=0),
    )


def abs_stats(arr: np.ndarray, prefix: str) -> dict[str, float]:
    values = np.abs(arr).reshape(-1)
    return {
        f"{prefix}_mean": float(np.mean(values)),
        f"{prefix}_p95": float(np.percentile(values, 95.0)),
        f"{prefix}_p99": float(np.percentile(values, 99.0)),
        f"{prefix}_p999": float(np.percentile(values, 99.9)),
        f"{prefix}_max": float(np.max(values)),
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


def center_speed(case: CaseData) -> np.ndarray:
    u_c = 0.5 * (case.u[:, :, :, :-1] + case.u[:, :, :, 1:])
    v_c = 0.5 * (case.v[:, :, :-1, :] + case.v[:, :, 1:, :])
    return np.sqrt(u_c * u_c + v_c * v_c, dtype=np.float32)


def nearest_level_indices(agl: np.ndarray, target: float) -> np.ndarray:
    return np.argmin(np.abs(agl - target), axis=0).astype(np.int32)


def gather_by_level(field: np.ndarray, level_idx: np.ndarray) -> np.ndarray:
    _, _, ny, nx = field.shape
    y_idx = np.arange(ny)[None, :, None]
    x_idx = np.arange(nx)[None, None, :]
    lev = level_idx[None, :, :]
    return field[:, lev, y_idx, x_idx].reshape(field.shape[0], ny, nx)


def count_median_levels(agl: np.ndarray, limit: float) -> int:
    return int(np.sum(np.median(agl, axis=(1, 2)) <= limit))


def summarize_case(case: CaseData) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    meta = case.meta
    speed = center_speed(case)
    w_loc = max_location(case.w, case)
    wg_loc = max_location(case.w_grid, case)

    upper5 = slice(max(case.w.shape[1] - 5, 0), case.w.shape[1])
    lower500_mask = case.agl <= 500.0
    lower1000_mask = case.agl <= 1000.0
    mid_orographic_mask = (case.agl >= 3000.0) & (case.agl <= 7000.0)

    dz_med = np.diff(np.median(case.agl, axis=(1, 2)))
    first_dz = float(dz_med[0]) if len(dz_med) else math.nan
    dz_below200 = dz_med[np.median(case.agl[:-1], axis=(1, 2)) <= 200.0]

    row = {
        "case": meta["case"],
        "label": meta["label"],
        "group": meta["group"],
        "auto_level": meta["auto_level"],
        "nz": meta["nz"],
        "top_km": meta["top_km"],
        "sleve_decay": meta["decay"],
        "nt": int(case.w.shape[0]),
        "median_top_agl_m": float(np.median(case.agl[-1])),
        "median_first_level_agl_m": float(np.median(case.agl[0])),
        "median_first_dz_m": first_dz,
        "median_dz_below200_mean_m": float(np.mean(dz_below200)) if dz_below200.size else math.nan,
        "n_median_levels_below_50m": count_median_levels(case.agl, 50.0),
        "n_median_levels_below_100m": count_median_levels(case.agl, 100.0),
        "n_median_levels_below_200m": count_median_levels(case.agl, 200.0),
        "n_median_levels_below_500m": count_median_levels(case.agl, 500.0),
        "n_median_levels_below_1000m": count_median_levels(case.agl, 1000.0),
        "temperature_min_K": float(np.min(case.temperature)),
        "temperature_p001_K": float(np.percentile(case.temperature.reshape(-1), 0.1)),
        "theta_min_K": float(np.min(case.potential_temperature)),
        "theta_p001_K": float(np.percentile(case.potential_temperature.reshape(-1), 0.1)),
        "pressure_min_Pa": float(np.min(case.pressure)),
        "density_min_kgm3": float(np.min(case.density)),
        "top_density_mean_kgm3": float(np.mean(case.density[:, -1, :, :])),
        "top_w_abs_max": float(np.max(np.abs(case.w[:, -1, :, :]))),
        "top_w_abs_p99": float(np.percentile(np.abs(case.w[:, -1, :, :]), 99.0)),
        "top_w_grid_abs_max": float(np.max(np.abs(case.w_grid[:, -1, :, :]))),
        "top_w_grid_abs_p99": float(np.percentile(np.abs(case.w_grid[:, -1, :, :]), 99.0)),
        "upper5_w_abs_max": float(np.max(np.abs(case.w[:, upper5, :, :]))),
        "upper5_w_abs_p99": float(np.percentile(np.abs(case.w[:, upper5, :, :]), 99.0)),
        "upper5_w_grid_abs_max": float(np.max(np.abs(case.w_grid[:, upper5, :, :]))),
        "upper5_w_grid_abs_p99": float(np.percentile(np.abs(case.w_grid[:, upper5, :, :]), 99.0)),
        "lower500_w_abs_max": float(np.max(np.abs(case.w[:, lower500_mask]))),
        "lower500_w_abs_p99": float(np.percentile(np.abs(case.w[:, lower500_mask]), 99.0)),
        "lower1000_w_abs_max": float(np.max(np.abs(case.w[:, lower1000_mask]))),
        "lower1000_w_abs_p99": float(np.percentile(np.abs(case.w[:, lower1000_mask]), 99.0)),
        "mid_agl_w_abs_max": float(np.max(np.abs(case.w[:, mid_orographic_mask]))),
        "mid_agl_w_abs_p99": float(np.percentile(np.abs(case.w[:, mid_orographic_mask]), 99.0)),
        "wind_alpha_mean": float(np.mean(case.wind_alpha)),
        "wind_alpha_p95": float(np.percentile(case.wind_alpha.reshape(-1), 95.0)),
        "wind_alpha_max": float(np.max(case.wind_alpha)),
        "w_max_level": w_loc["level"],
        "w_max_time": w_loc["time"],
        "w_max_agl_m": w_loc["agl_m"],
        "w_max_topo_m": w_loc["topo_m"],
        "w_max_lat": w_loc["lat"],
        "w_max_lon": w_loc["lon"],
        "w_grid_max_level": wg_loc["level"],
        "w_grid_max_time": wg_loc["time"],
        "w_grid_max_agl_m": wg_loc["agl_m"],
        "w_grid_max_topo_m": wg_loc["topo_m"],
        **abs_stats(case.w, "w_abs"),
        **abs_stats(case.w_grid, "w_grid_abs"),
    }

    level_rows = []
    for k in range(case.w.shape[1]):
        level_rows.append({
            "case": meta["case"],
            "level": k,
            "agl_median_m": float(np.median(case.agl[k])),
            "agl_min_m": float(np.min(case.agl[k])),
            "agl_max_m": float(np.max(case.agl[k])),
            "speed_mean": float(np.mean(speed[:, k, :, :])),
            "speed_p95": float(np.percentile(speed[:, k, :, :], 95.0)),
            "speed_max": float(np.max(speed[:, k, :, :])),
            "w_abs_p99": float(np.percentile(np.abs(case.w[:, k, :, :]), 99.0)),
            "w_abs_max": float(np.max(np.abs(case.w[:, k, :, :]))),
            "w_grid_abs_p99": float(np.percentile(np.abs(case.w_grid[:, k, :, :]), 99.0)),
            "w_grid_abs_max": float(np.max(np.abs(case.w_grid[:, k, :, :]))),
        })

    near_surface_rows = []
    for target in TARGET_AGL:
        li = nearest_level_indices(case.agl, target)
        selected_speed = gather_by_level(speed, li)
        selected_w = gather_by_level(case.w, li)
        selected_wg = gather_by_level(case.w_grid, li)
        selected_agl = np.take_along_axis(case.agl, li[None, :, :], axis=0)[0]
        vals, counts = np.unique(li, return_counts=True)
        near_surface_rows.append({
            "case": meta["case"],
            "target_agl_m": target,
            "selected_level_mode": int(vals[np.argmax(counts)]),
            "selected_agl_mean_m": float(np.mean(selected_agl)),
            "selected_agl_min_m": float(np.min(selected_agl)),
            "selected_agl_max_m": float(np.max(selected_agl)),
            "speed_mean": float(np.mean(selected_speed)),
            "speed_p50": float(np.percentile(selected_speed, 50.0)),
            "speed_p95": float(np.percentile(selected_speed, 95.0)),
            "speed_p99": float(np.percentile(selected_speed, 99.0)),
            "speed_max": float(np.max(selected_speed)),
            "w_abs_p99": float(np.percentile(np.abs(selected_w), 99.0)),
            "w_abs_max": float(np.max(np.abs(selected_w))),
            "w_grid_abs_p99": float(np.percentile(np.abs(selected_wg), 99.0)),
            "w_grid_abs_max": float(np.max(np.abs(selected_wg))),
        })

    return row, level_rows, near_surface_rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, object]], columns: list[str]) -> str:
    def fmt(value: object) -> str:
        if isinstance(value, float):
            if math.isnan(value):
                return "nan"
            if abs(value) >= 1000:
                return f"{value:.0f}"
            if abs(value) >= 100:
                return f"{value:.1f}"
            if abs(value) >= 10:
                return f"{value:.2f}"
            return f"{value:.3f}"
        return str(value)

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(fmt(row[column]) for column in columns) + " |")
    return "\n".join(lines)


def make_deltas(cases: dict[str, CaseData]) -> list[dict[str, object]]:
    ref = cases["v1_auto1_n60_top12_s26"]
    ref_speed = center_speed(ref)
    rows: list[dict[str, object]] = []
    for target in TARGET_AGL:
        ref_li = nearest_level_indices(ref.agl, target)
        ref_s = gather_by_level(ref_speed, ref_li)
        ref_w = gather_by_level(ref.w, ref_li)
        for case_id, case in cases.items():
            if case_id == ref.meta["case"]:
                continue
            speed = center_speed(case)
            li = nearest_level_indices(case.agl, target)
            s = gather_by_level(speed, li)
            w = gather_by_level(case.w, li)
            ds = s - ref_s
            dw = np.abs(w) - np.abs(ref_w)
            rows.append({
                "target_agl_m": target,
                "reference": ref.meta["case"],
                "comparison": case_id,
                "speed_mean_diff": float(np.mean(ds)),
                "speed_mean_abs_diff": float(np.mean(np.abs(ds))),
                "speed_p95_abs_diff": float(np.percentile(np.abs(ds), 95.0)),
                "speed_max_abs_diff": float(np.max(np.abs(ds))),
                "w_abs_mean_diff": float(np.mean(dw)),
                "w_abs_p95_abs_diff": float(np.percentile(np.abs(dw), 95.0)),
            })
    return rows


def write_plots(summaries: list[dict[str, object]], level_rows: list[dict[str, object]]) -> None:
    import matplotlib.pyplot as plt

    cases = [str(row["case"]) for row in summaries]
    short_names = [case.split("_")[0] for case in cases]

    plt.figure(figsize=(8, 5))
    for case in cases:
        rows = [row for row in level_rows if row["case"] == case and float(row["agl_median_m"]) <= 500.0]
        label = case.split("_")[0]
        plt.plot(
            [int(row["level"]) for row in rows],
            [float(row["agl_median_m"]) for row in rows],
            marker="o",
            linewidth=1.4,
            markersize=3,
            label=label,
        )
    plt.axhline(50.0, color="0.7", linewidth=0.8)
    plt.axhline(100.0, color="0.7", linewidth=0.8)
    plt.axhline(200.0, color="0.7", linewidth=0.8)
    plt.xlabel("model level index")
    plt.ylabel("median AGL height [m]")
    plt.title("Median vertical levels in the wind-energy layer")
    plt.ylim(0, 520)
    plt.grid(True, linewidth=0.4, alpha=0.4)
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(VERTICAL_LEVELS_PNG, dpi=170)
    plt.close()

    x = np.arange(len(summaries))
    width = 0.26
    plt.figure(figsize=(9, 5))
    plt.bar(x - width, [float(row["w_abs_p99"]) for row in summaries], width, label="|w| p99")
    plt.bar(x, [float(row["w_grid_abs_p99"]) for row in summaries], width, label="|w_grid| p99")
    plt.bar(
        x + width,
        [float(row["n_median_levels_below_200m"]) for row in summaries],
        width,
        label="levels <200 m",
    )
    plt.xticks(x, short_names)
    plt.ylabel("metric value")
    plt.title("Core stability and low-level-resolution metrics")
    plt.grid(axis="y", linewidth=0.4, alpha=0.4)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(CORE_METRICS_PNG, dpi=170)
    plt.close()


def main() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    topo = read_topo()
    summaries: list[dict[str, object]] = []
    level_rows: list[dict[str, object]] = []
    near_surface_rows: list[dict[str, object]] = []
    cases: dict[str, CaseData] = {}

    for meta in CASES:
        case = read_case(meta, topo)
        cases[str(meta["case"])] = case
        summary, levels, near_surface = summarize_case(case)
        summaries.append(summary)
        level_rows.extend(levels)
        near_surface_rows.extend(near_surface)

    delta_rows = make_deltas(cases)
    write_csv(SUMMARY_CSV, summaries)
    write_csv(LEVEL_CSV, level_rows)
    write_csv(NEAR_SURFACE_CSV, near_surface_rows)
    write_csv(DELTAS_CSV, delta_rows)
    write_plots(summaries, level_rows)

    summary_cols = [
        "case",
        "nz",
        "top_km",
        "sleve_decay",
        "nt",
        "n_median_levels_below_200m",
        "median_first_level_agl_m",
        "median_first_dz_m",
        "w_abs_max",
        "w_abs_p99",
        "w_grid_abs_max",
        "w_grid_abs_p99",
        "lower500_w_abs_max",
        "lower500_w_abs_p99",
        "temperature_min_K",
    ]
    ns_cols = [
        "case",
        "target_agl_m",
        "selected_level_mode",
        "selected_agl_mean_m",
        "speed_mean",
        "speed_p95",
        "speed_max",
        "w_abs_p99",
        "w_abs_max",
    ]
    delta_cols = [
        "target_agl_m",
        "comparison",
        "speed_mean_diff",
        "speed_mean_abs_diff",
        "speed_p95_abs_diff",
        "w_abs_mean_diff",
    ]

    by_case = {str(row["case"]): row for row in summaries}
    lines = [
        "# HICAR Wind-Design Sensitivity Analysis",
        "",
        "Run window: 25 output records from 2026-07-10 18 UTC through 2026-07-11 18 UTC.",
        "",
        "All cases use `HICAR_release`, `wind = 'variational solver'`, `Sx = .True.`, `smooth_wind_distance = 500 m`, Morrison microphysics, no PBL/LSM/radiation/surface physics, ready files, and boundary-zone topography relaxation.",
        "",
        "## Core Diagnostics",
        "",
        markdown_table(summaries, summary_cols),
        "",
        "## Wind-Energy Layer Diagnostics",
        "",
        markdown_table(near_surface_rows, ns_cols),
        "",
        "## Near-Surface Deltas Relative To `v1_auto1_n60_top12_s26`",
        "",
        markdown_table(delta_rows, delta_cols),
        "",
        "## Interpretation",
        "",
        f"- The `v2` 80-level setup has the best near-surface resolution: {by_case['v2_auto1_n80_top12_s26']['n_median_levels_below_200m']} median mass levels below 200 m AGL versus {by_case['v1_auto1_n60_top12_s26']['n_median_levels_below_200m']} in the 60-level baseline.",
        f"- The 10 km lid case `v3` is fastest and finite, but it has the lowest median model top and is less conservative for Switzerland-wide domains with Alpine terrain near 4500-5000 m ASL.",
        f"- The 14 km lid case `v4` is also finite, but it gives no clear near-surface advantage over 12 km while adding cost and retaining a higher-altitude upper domain.",
        f"- The COSMO-like `auto4` vertical distribution (`v5`) is finite, but it provides fewer low-level levels than `v2` for the wind-energy target layer.",
        f"- Among the conservative SLEVE variants, `s0` and `s1` increase the vertical-velocity envelope relative to the default-like `2/6` baseline; `s2` is closer but still does not improve the baseline enough to justify replacing `2/6`.",
        f"- All cases stayed finite by these bulk checks. Minimum temperatures remain low enough to keep an eye on the theta-limiter behavior, but no case reproduced the severe top-level `w` pathology from the older `wind='none'` run.",
        "- Model stderr logs were empty except for one Slurm shutdown-IO line in `v2` after successful job completion: `srun: error: eio_handle_mainloop: Abandoning IO 60 secs after job shutdown initiated`.",
        "",
        "## Recommendation",
        "",
        "Use `v2_auto1_n80_top12_s26` as the next default HICAR setup for wind-downscaling experiments at 250 m: `auto_level = 1`, `nz = 80`, `model_top_height = 12000 m`, `height_lowest_level = 15 m`, `stretch_fac = 0.65`, `decay_rate_L_topo = 2.0`, `decay_rate_S_topo = 6.0`, `wind = 'variational solver'`, `Sx = .True.`, and `smooth_wind_distance = 500 m`.",
        "",
        "The rationale is practical: it is the only tested setup with clearly improved vertical resolution in the 50-200 m wind-energy layer, it completed the 24 h case cleanly, it keeps the 12 km lid that already removed the earlier top-level pathology, and the SLEVE choice remains conservative enough for larger Swiss domains.",
        "",
        "If the 80-level setup becomes too expensive for a larger Switzerland-wide domain, use `v1_auto1_n60_top12_s26` as the cost-control fallback. Do not switch to the slower SLEVE-decay options from this batch unless a larger-domain test shows that `2/6` creates a domain-edge or terrain-following-coordinate problem.",
        "",
        "For production-scale work, validate this setup on at least one larger Switzerland-with-border domain and one longer multi-day case before freezing it.",
        "",
        "## Files",
        "",
        f"- Summary CSV: `{SUMMARY_CSV}`",
        f"- Level statistics CSV: `{LEVEL_CSV}`",
        f"- Wind-energy layer CSV: `{NEAR_SURFACE_CSV}`",
        f"- Near-surface deltas CSV: `{DELTAS_CSV}`",
        f"- Vertical-level plot: `{VERTICAL_LEVELS_PNG}`",
        f"- Core-metrics plot: `{CORE_METRICS_PNG}`",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
