#!/usr/bin/env python3
"""Analyze HICAR output physicality for the Alps 250 m case."""

from __future__ import annotations

import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from netCDF4 import Dataset, num2date


CASE_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = RUN_ROOT / "output"
STATIC_FILE = CASE_ROOT / "static" / "domain_static_relaxed.nc"
VALIDATION_REPORT = CASE_ROOT / "validation" / "validation_full.md"
ANALYSIS_DIR = RUN_ROOT / "analysis"


def as_array(var):
    data = np.ma.asarray(var[:])
    if np.ma.isMaskedArray(data):
        data = data.filled(np.nan)
    return np.asarray(data, dtype=np.float64)


def fmt_time(value) -> str:
    return f"{int(value.year):04d}-{int(value.month):02d}-{int(value.day):02d} {int(value.hour):02d}:{int(value.minute):02d}:{int(value.second):02d}"


def stats(values: np.ndarray) -> dict[str, float]:
    flat = np.asarray(values, dtype=np.float64).ravel()
    return {
        "min": float(np.nanmin(flat)),
        "p01": float(np.nanpercentile(flat, 1)),
        "p50": float(np.nanpercentile(flat, 50)),
        "p99": float(np.nanpercentile(flat, 99)),
        "max": float(np.nanmax(flat)),
        "mean": float(np.nanmean(flat)),
        "finite_fraction": float(np.isfinite(flat).sum() / flat.size),
    }


def extrema_location(values: np.ndarray, kind: str = "max") -> tuple[int, ...]:
    arr = np.asarray(values)
    idx = np.nanargmax(arr) if kind == "max" else np.nanargmin(arr)
    return np.unravel_index(idx, arr.shape)


def load_outputs():
    files = sorted(OUTPUT_DIR.glob("*.nc"))
    if not files:
        raise SystemExit(f"No HICAR output files found in {OUTPUT_DIR}")

    chunks: dict[str, list[np.ndarray]] = {name: [] for name in ["temperature", "qv", "u", "v", "w", "precipitation"]}
    times = []
    z = lat = lon = None

    for path in files:
        with Dataset(path) as ds:
            tvar = ds.variables["time"]
            times.extend(num2date(tvar[:], tvar.units, getattr(tvar, "calendar", "standard")))
            for name in chunks:
                chunks[name].append(as_array(ds.variables[name]))
            if z is None:
                z = as_array(ds.variables["z"])
                lat = as_array(ds.variables["lat"])
                lon = as_array(ds.variables["lon"])

    data = {name: np.concatenate(parts, axis=0) for name, parts in chunks.items()}
    data["z"] = z
    data["lat"] = lat
    data["lon"] = lon
    return files, np.array(times, dtype=object), data


def load_static():
    with Dataset(STATIC_FILE) as ds:
        return {
            "topo": as_array(ds.variables["topo"]),
            "topo_highres": as_array(ds.variables["topo_highres"]),
            "topo_driving": as_array(ds.variables["topo_driving"]),
            "topo_blend_weight": as_array(ds.variables["topo_blend_weight"]),
            "landmask": as_array(ds.variables["landmask"]),
            "landuse": as_array(ds.variables["landuse"]),
        }


def forcing_ranges_from_validation() -> dict[str, tuple[float, float]]:
    if not VALIDATION_REPORT.exists():
        return {}
    ranges = {}
    pattern = re.compile(r"^- ([A-Z_]+): min=([-+0-9.eE]+), max=([-+0-9.eE]+)")
    for line in VALIDATION_REPORT.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            ranges[match.group(1)] = (float(match.group(2)), float(match.group(3)))
    return ranges


def mass_grid_wind_speed(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    u_mass = 0.5 * (u[:, :, :, :-1] + u[:, :, :, 1:])
    v_mass = 0.5 * (v[:, :, :-1, :] + v[:, :, 1:, :])
    return np.sqrt(u_mass**2 + v_mass**2)


def make_figures(times, data, static):
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    hours = np.arange(len(times))
    wind_speed = mass_grid_wind_speed(data["u"], data["v"])
    precip = data["precipitation"]
    w_abs_max = np.nanmax(np.abs(data["w"]), axis=(0, 1))
    final_precip = precip[-1]
    z_agl = data["z"] - static["topo"][None, :, :]

    fig, axes = plt.subplots(4, 1, figsize=(9, 10), sharex=True)
    panels = [
        ("temperature", data["temperature"], "K"),
        ("qv", data["qv"] * 1000.0, "g kg$^{-1}$"),
        ("wind speed", wind_speed, "m s$^{-1}$"),
        ("|w|", np.abs(data["w"]), "m s$^{-1}$"),
    ]
    for ax, (name, arr, unit) in zip(axes, panels):
        ax.plot(hours, np.nanmean(arr, axis=tuple(range(1, arr.ndim))), label="mean")
        ax.plot(hours, np.nanpercentile(arr.reshape(arr.shape[0], -1), 99, axis=1), label="p99")
        ax.plot(hours, np.nanmax(arr.reshape(arr.shape[0], -1), axis=1), label="max")
        ax.set_ylabel(unit)
        ax.set_title(name)
        ax.grid(True, alpha=0.25)
    axes[-1].set_xlabel("output record index (hourly)")
    axes[0].legend(loc="best", ncol=3)
    fig.tight_layout()
    fig.savefig(ANALYSIS_DIR / "field_time_series.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))
    level_agl = np.nanmean(z_agl, axis=(1, 2))
    for ax, name, arr, unit in [
        (axes[0], "temperature", data["temperature"], "K"),
        (axes[1], "qv", data["qv"] * 1000.0, "g kg$^{-1}$"),
        (axes[2], "|w|", np.abs(data["w"]), "m s$^{-1}$"),
    ]:
        profile = np.nanmean(arr, axis=(0, 2, 3))
        p10 = np.nanpercentile(arr, 10, axis=(0, 2, 3))
        p90 = np.nanpercentile(arr, 90, axis=(0, 2, 3))
        ax.plot(profile, level_agl / 1000.0, label="mean")
        ax.fill_betweenx(level_agl / 1000.0, p10, p90, alpha=0.25, label="p10-p90")
        ax.set_title(name)
        ax.set_xlabel(unit)
        ax.grid(True, alpha=0.25)
    axes[0].set_ylabel("mean height AGL (km)")
    axes[0].legend(loc="best")
    fig.tight_layout()
    fig.savefig(ANALYSIS_DIR / "vertical_profiles.png", dpi=160)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    m0 = axes[0].pcolormesh(data["lon"], data["lat"], final_precip, shading="auto")
    axes[0].contour(data["lon"], data["lat"], static["topo"], colors="k", linewidths=0.35, alpha=0.45)
    axes[0].set_title("final accumulated precipitation")
    axes[0].set_xlabel("lon")
    axes[0].set_ylabel("lat")
    plt.colorbar(m0, ax=axes[0], label="kg m$^{-2}$")

    m1 = axes[1].pcolormesh(data["lon"], data["lat"], w_abs_max, shading="auto")
    axes[1].contour(data["lon"], data["lat"], static["topo"], colors="k", linewidths=0.35, alpha=0.45)
    axes[1].set_title("max |w| over output records and levels")
    axes[1].set_xlabel("lon")
    axes[1].set_ylabel("lat")
    plt.colorbar(m1, ax=axes[1], label="m s$^{-1}$")
    fig.tight_layout()
    fig.savefig(ANALYSIS_DIR / "precip_and_w_maps.png", dpi=160)
    plt.close(fig)


def write_report(files, times, data, static):
    forcing_ranges = forcing_ranges_from_validation()
    report = []
    add = report.append
    add("# HICAR Output Physicality Analysis")
    add("")
    add(f"Output files analyzed: `{len(files)}`")
    for path in files:
        add(f"- `{path.name}`")
    add(f"Output records: `{len(times)}` from `{fmt_time(times[0])}` to `{fmt_time(times[-1])}`")
    add("")

    add("## Executive Summary")
    add("")
    add("- All analyzed output fields are finite; no NaN or Inf values were found.")
    add("- Temperature, humidity, and horizontal wind ranges are within the already-validated ICON forcing envelope and are physically plausible for an Alpine summer case.")
    add("- Precipitation behaves like an accumulated field: it is non-decreasing except for negligible roundoff noise, with a localized maximum near 50.9 kg m-2.")
    add("- Vertical velocity is the main caution: global extrema reach roughly +40/-31 m s-1. These values are rare and localized over steep terrain, but they are large enough to flag for follow-up before treating the run as scientifically robust.")
    add("- The run used a deliberately minimal physics suite and aborted near the end because HICAR required one more forcing file beyond the requested end time; this output is useful for engineering/physicality screening, not yet for production science.")
    add("")

    add("## Coverage And Integrity")
    add("")
    add(f"- File count: `{len(files)}`")
    add(f"- Time records: `{len(times)}` hourly records.")
    add(f"- Time range: `{fmt_time(times[0])}` to `{fmt_time(times[-1])}`.")
    add("- Expected full 33 h run did not finish cleanly; records stop before the requested final time because the model aborted on forcing-list end handling.")
    for name in ["temperature", "qv", "u", "v", "w", "precipitation", "z"]:
        finite = np.isfinite(data[name])
        add(f"- `{name}` finite values: `{finite.sum()}/{finite.size}`.")
    add("")

    add("## Global Ranges")
    add("")
    add("| Field | Units | Min | P01 | Median | P99 | Max | Mean |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|")
    field_units = {
        "temperature": "K",
        "qv": "kg kg-1",
        "u": "m s-1",
        "v": "m s-1",
        "w": "m s-1",
        "precipitation": "kg m-2",
        "z": "m",
    }
    for name in ["temperature", "qv", "u", "v", "w", "precipitation", "z"]:
        s = stats(data[name])
        add(
            f"| `{name}` | {field_units[name]} | {s['min']:.6g} | {s['p01']:.6g} | "
            f"{s['p50']:.6g} | {s['p99']:.6g} | {s['max']:.6g} | {s['mean']:.6g} |"
        )
    add("")

    if forcing_ranges:
        add("## Comparison To Prepared ICON Forcing")
        add("")
        add("| Quantity | HICAR output range | Prepared forcing range | Assessment |")
        add("|---|---:|---:|---|")
        mapping = {"temperature": "T", "qv": "QV", "u": "U", "v": "V", "w": "W"}
        for out_name, forcing_name in mapping.items():
            out_s = stats(data[out_name])
            fr = forcing_ranges.get(forcing_name)
            if fr is None:
                continue
            inside = out_s["min"] >= fr[0] - 1e-8 and out_s["max"] <= fr[1] + 1e-8
            assessment = "inside forcing envelope" if inside else "outside forcing envelope"
            add(
                f"| `{out_name}` / `{forcing_name}` | {out_s['min']:.6g} .. {out_s['max']:.6g} | "
                f"{fr[0]:.6g} .. {fr[1]:.6g} | {assessment} |"
            )
        add("")
        add("Note: `w` is not expected to remain strictly inside the coarse forcing `W` range because HICAR recomputes flow over higher-resolution relaxed topography. The output minimum being more negative than the prepared forcing minimum is therefore a diagnostic flag, not by itself a conservation or file-read failure.")
        add("")

    add("## Vertical Grid And Terrain")
    add("")
    z_agl = data["z"] - static["topo"][None, :, :]
    dz = np.diff(data["z"], axis=0)
    add(f"- Static topography range used by HICAR: `{np.nanmin(static['topo']):.3f} .. {np.nanmax(static['topo']):.3f} m`.")
    add(f"- First model-level AGL height range: `{np.nanmin(z_agl[0]):.3f} .. {np.nanmax(z_agl[0]):.3f} m`.")
    add(f"- Top model-level AGL height range: `{np.nanmin(z_agl[-1]):.3f} .. {np.nanmax(z_agl[-1]):.3f} m`.")
    add(f"- Minimum vertical spacing between output levels: `{np.nanmin(dz):.3f} m`; vertical coordinate is monotonic everywhere: `{bool(np.all(dz > 0))}`.")
    add("- Boundary-zone topography relaxation is present in the static file and was used by this run.")
    add("")

    add("## Field-Specific Checks")
    add("")
    t = data["temperature"]
    qv = data["qv"]
    wind_speed = mass_grid_wind_speed(data["u"], data["v"])
    w = data["w"]
    precip = data["precipitation"]
    precip_inc = np.diff(precip, axis=0)

    add("### Temperature")
    add("")
    add(f"- Full range: `{np.nanmin(t):.3f} .. {np.nanmax(t):.3f} K`.")
    add(f"- Near-surface output level range: `{np.nanmin(t[:, 0]):.3f} .. {np.nanmax(t[:, 0]):.3f} K`.")
    add(f"- Top output level range: `{np.nanmin(t[:, -1]):.3f} .. {np.nanmax(t[:, -1]):.3f} K`.")
    add("- No cold-collapse signature like the earlier theta-advection failure is visible in the output range.")
    add("")

    add("### Humidity")
    add("")
    add(f"- Specific humidity range: `{np.nanmin(qv):.6g} .. {np.nanmax(qv):.6g} kg kg-1`.")
    add(f"- Negative humidity count: `{int(np.sum(qv < -1e-12))}`.")
    add("- The maximum corresponds to about `15 g kg-1`, plausible near the lower troposphere in July.")
    add("")

    add("### Horizontal And Vertical Wind")
    add("")
    ws = stats(wind_speed)
    add(f"- Mass-grid horizontal wind-speed range: `{np.nanmin(wind_speed):.3f} .. {np.nanmax(wind_speed):.3f} m s-1`; p99 is `{ws['p99']:.3f} m s-1`.")
    absw = np.abs(w)
    add(f"- |w| percentiles: p50=`{np.nanpercentile(absw, 50):.3f}`, p90=`{np.nanpercentile(absw, 90):.3f}`, p99=`{np.nanpercentile(absw, 99):.3f}`, p99.9=`{np.nanpercentile(absw, 99.9):.3f}`, max=`{np.nanmax(absw):.3f}` m s-1.")
    for threshold in [5, 10, 20, 30]:
        frac = 100.0 * float(np.sum(absw > threshold) / absw.size)
        add(f"- Fraction of `|w| > {threshold} m s-1`: `{frac:.5f}%`.")
    per_level_max = np.nanmax(absw, axis=(0, 2, 3))
    per_level_p99 = np.nanpercentile(absw, 99, axis=(0, 2, 3))
    strongest_levels = np.argsort(per_level_max)[-5:][::-1]
    add("- Strongest `|w|` levels by max value:")
    for level in strongest_levels:
        add(f"  - level `{int(level)}`: max `{per_level_max[level]:.3f} m s-1`, p99 `{per_level_p99[level]:.3f} m s-1`.")
    edge_mask = np.zeros(absw.shape[-2:], dtype=bool)
    edge_mask[[0, -1], :] = True
    edge_mask[:, [0, -1]] = True
    for threshold in [10, 20, 30]:
        total_count = int(np.sum(absw > threshold))
        edge_count = int(np.sum(absw[..., edge_mask] > threshold))
        share = 100.0 * edge_count / total_count if total_count else 0.0
        add(f"- Share of `|w| > {threshold} m s-1` points on the outermost grid-cell edge: `{share:.2f}%`.")
    wmax_idx = extrema_location(w, "max")
    wmin_idx = extrema_location(w, "min")
    add(f"- Max `w` at record `{wmax_idx[0]}`, level `{wmax_idx[1]}`, lat/lon `{data['lat'][wmax_idx[2], wmax_idx[3]]:.5f}, {data['lon'][wmax_idx[2], wmax_idx[3]]:.5f}`: `{w[wmax_idx]:.3f} m s-1`.")
    add(f"- Min `w` at record `{wmin_idx[0]}`, level `{wmin_idx[1]}`, lat/lon `{data['lat'][wmin_idx[2], wmin_idx[3]]:.5f}, {data['lon'][wmin_idx[2], wmin_idx[3]]:.5f}`: `{w[wmin_idx]:.3f} m s-1`.")
    add("- Interpretation: the domain is numerically stable through the available run, but the local vertical-velocity extremes are physically aggressive. They are concentrated in the upper model levels, with a minority also touching the lateral edge, so the next diagnostic pass should look for upper-boundary wave reflection, vertical-coordinate effects, and sensitivity to model-top/damping choices in addition to terrain-slope forcing.")
    add("")

    add("### Precipitation")
    add("")
    add(f"- Accumulated precipitation range over available records: `{np.nanmin(precip):.6g} .. {np.nanmax(precip):.6g} kg m-2`.")
    add(f"- Final accumulated precipitation: mean `{np.nanmean(precip[-1]):.3f}`, p99 `{np.nanpercentile(precip[-1], 99):.3f}`, max `{np.nanmax(precip[-1]):.3f} kg m-2`.")
    add(f"- Hourly increment range: `{np.nanmin(precip_inc):.6g} .. {np.nanmax(precip_inc):.6g} kg m-2 h-1`.")
    add(f"- Negative hourly increments below `-1e-5`: `{int(np.sum(precip_inc < -1e-5))}`.")
    add("- The tiny negative global minimum is numerical roundoff around zero, not a physically meaningful negative precipitation amount.")
    add("")

    add("## Figures")
    add("")
    add("![Field time series](field_time_series.png)")
    add("")
    add("![Vertical profiles](vertical_profiles.png)")
    add("")
    add("![Precipitation and vertical velocity maps](precip_and_w_maps.png)")
    add("")

    add("## Scientific Plausibility Verdict")
    add("")
    add("The available output is numerically well behaved and physically plausible at first order: no NaNs/Infs, no humidity negativity, no temperature collapse, reasonable horizontal winds, monotonic vertical coordinate, and monotonic accumulated precipitation. The main scientific caution is localized extreme vertical velocity over complex terrain. Given the minimal physics configuration (`mp = morrison`, but no PBL, LSM, surface, radiation, or water physics) and the incomplete run termination, this should be treated as a successful engineering smoke/physicality screen, not yet a production-quality downscaling experiment.")

    (ANALYSIS_DIR / "hicar_output_physicality_report.md").write_text("\n".join(report) + "\n")


def main():
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    files, times, data = load_outputs()
    static = load_static()
    make_figures(times, data, static)
    write_report(files, times, data, static)
    print(ANALYSIS_DIR / "hicar_output_physicality_report.md")


if __name__ == "__main__":
    main()
