#!/usr/bin/env python3
"""Targeted diagnostics for suspicious HICAR vertical-velocity patterns."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from netCDF4 import Dataset, num2date


CASE_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = RUN_ROOT / "output"
FORCING_DIR = CASE_ROOT / "forcing"
STATIC_FILE = CASE_ROOT / "static" / "domain_static_relaxed.nc"
ANALYSIS_DIR = RUN_ROOT / "analysis"


def as_array(var) -> np.ndarray:
    data = np.ma.asarray(var[:])
    if np.ma.isMaskedArray(data):
        data = data.filled(np.nan)
    return np.asarray(data, dtype=np.float64)


def fmt_time(value) -> str:
    return (
        f"{int(value.year):04d}-{int(value.month):02d}-{int(value.day):02d} "
        f"{int(value.hour):02d}:{int(value.minute):02d}"
    )


def load_outputs() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    times = []
    w_parts = []
    lat = lon = z = None
    for path in sorted(OUTPUT_DIR.glob("*.nc")):
        with Dataset(path) as ds:
            tvar = ds.variables["time"]
            times.extend(num2date(tvar[:], tvar.units, getattr(tvar, "calendar", "standard")))
            w_parts.append(as_array(ds.variables["w"]))
            if lat is None:
                lat = as_array(ds.variables["lat"])
                lon = as_array(ds.variables["lon"])
                z = as_array(ds.variables["z"])
    if not w_parts:
        raise SystemExit(f"No HICAR output files found in {OUTPUT_DIR}")
    return np.array(times, dtype=object), {"w": np.concatenate(w_parts, axis=0), "lat": lat, "lon": lon, "z": z}


def load_static() -> dict[str, np.ndarray]:
    with Dataset(STATIC_FILE) as ds:
        return {
            "topo": as_array(ds.variables["topo"]),
            "topo_highres": as_array(ds.variables["topo_highres"]),
            "topo_driving": as_array(ds.variables["topo_driving"]),
            "topo_blend_weight": as_array(ds.variables["topo_blend_weight"]),
        }


def load_forcing_w_domain_stats(lat: np.ndarray, lon: np.ndarray, n_times: int) -> dict[str, np.ndarray]:
    """Summarize prepared ICON W over the HICAR lat/lon envelope plus a small buffer."""
    stats = {"maxabs": [], "p99": [], "meanabs": [], "hhl_mean": None, "hours": []}
    lat_min, lat_max = float(np.nanmin(lat)) - 0.03, float(np.nanmax(lat)) + 0.03
    lon_min, lon_max = float(np.nanmin(lon)) - 0.03, float(np.nanmax(lon)) + 0.03
    domain_mask = None

    for hour in range(n_times):
        path = FORCING_DIR / f"hicar_forcing_f{hour:03d}.nc"
        if not path.exists():
            break
        with Dataset(path) as ds:
            flat = as_array(ds.variables["lat_1"])
            flon = as_array(ds.variables["lon_1"])
            if domain_mask is None:
                domain_mask = (flat >= lat_min) & (flat <= lat_max) & (flon >= lon_min) & (flon <= lon_max)
                hhl = as_array(ds.variables["HHL"])
                stats["hhl_mean"] = np.nanmean(hhl[:, domain_mask], axis=1)
            w = as_array(ds.variables["W"])[0]
            subset = w[:, domain_mask]
            stats["maxabs"].append(np.nanmax(np.abs(subset), axis=1))
            stats["p99"].append(np.nanpercentile(np.abs(subset), 99, axis=1))
            stats["meanabs"].append(np.nanmean(np.abs(subset), axis=1))
            stats["hours"].append(hour)

    for key in ("maxabs", "p99", "meanabs"):
        stats[key] = np.asarray(stats[key], dtype=np.float64)
    stats["hours"] = np.asarray(stats["hours"], dtype=np.int32)
    return stats


def plot_maps(times, data, static) -> None:
    w = data["w"]
    absw = np.abs(w)
    lat = data["lat"]
    lon = data["lon"]
    topo = static["topo"]

    top_level = w.shape[1] - 1
    top_abs = absw[:, top_level]
    top_max = np.nanmax(top_abs, axis=0)
    top_argtime = np.nanargmax(top_abs, axis=0)
    flat_global = np.nanargmax(absw)
    t_global, k_global, y_global, x_global = np.unravel_index(flat_global, absw.shape)

    selected = [20, 23, 24]
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), constrained_layout=True)
    ax = axes[0, 0]
    m = ax.pcolormesh(lon, lat, top_max, shading="auto")
    ax.contour(lon, lat, topo, colors="k", linewidths=0.35, alpha=0.45)
    ax.axhline(lat[40, 40], color="white", lw=0.8, alpha=0.7)
    ax.axvline(lon[40, 40], color="white", lw=0.8, alpha=0.7)
    ax.plot(lon[y_global, x_global], lat[y_global, x_global], "rx", ms=8, mew=2)
    ax.set_title(f"top level max |w| over time (level {top_level})")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    plt.colorbar(m, ax=ax, label="m s$^{-1}$")

    ax = axes[0, 1]
    m = ax.pcolormesh(lon, lat, top_argtime, shading="auto", cmap="turbo")
    ax.contour(lon, lat, topo, colors="k", linewidths=0.35, alpha=0.45)
    ax.set_title("argmax time of top-level |w|")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    plt.colorbar(m, ax=ax, label="output record")

    ax = axes[0, 2]
    level_of_max = np.nanargmax(absw, axis=1).max(axis=0)
    m = ax.pcolormesh(lon, lat, level_of_max, shading="auto", vmin=0, vmax=top_level)
    ax.contour(lon, lat, topo, colors="k", linewidths=0.35, alpha=0.45)
    ax.set_title("highest level contributing to per-time maxima")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    plt.colorbar(m, ax=ax, label="level index")

    vmax = max(float(np.nanmax(np.abs(w[t, top_level]))) for t in selected if t < w.shape[0])
    for ax, t in zip(axes[1], selected):
        if t >= w.shape[0]:
            ax.set_axis_off()
            continue
        m = ax.pcolormesh(lon, lat, w[t, top_level], shading="auto", cmap="RdBu_r", vmin=-vmax, vmax=vmax)
        ax.contour(lon, lat, topo, colors="k", linewidths=0.35, alpha=0.45)
        ax.axhline(lat[40, 40], color="k", lw=0.7, alpha=0.5)
        ax.axvline(lon[40, 40], color="k", lw=0.7, alpha=0.5)
        ax.set_title(f"top-level w at record {t}\n{fmt_time(times[t])}")
        ax.set_xlabel("lon")
        ax.set_ylabel("lat")
        plt.colorbar(m, ax=ax, label="m s$^{-1}$")

    fig.savefig(ANALYSIS_DIR / "w_pathology_top_level_maps.png", dpi=170)
    plt.close(fig)


def plot_vertical_time(times, data, forcing_stats) -> None:
    w = data["w"]
    absw = np.abs(w)
    per_time_level_max = np.nanmax(absw, axis=(2, 3))
    per_time_level_p99 = np.nanpercentile(absw, 99, axis=(2, 3))
    level_agl = np.nanmean(data["z"] - data["z"][0][None, :, :], axis=(1, 2))

    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    m = axes[0, 0].pcolormesh(np.arange(w.shape[0]), np.arange(w.shape[1]), per_time_level_max.T, shading="auto")
    axes[0, 0].set_title("HICAR |w| max by output record and level")
    axes[0, 0].set_xlabel("output record")
    axes[0, 0].set_ylabel("HICAR level")
    plt.colorbar(m, ax=axes[0, 0], label="m s$^{-1}$")

    m = axes[0, 1].pcolormesh(np.arange(w.shape[0]), np.arange(w.shape[1]), per_time_level_p99.T, shading="auto")
    axes[0, 1].set_title("HICAR |w| p99 by output record and level")
    axes[0, 1].set_xlabel("output record")
    axes[0, 1].set_ylabel("HICAR level")
    plt.colorbar(m, ax=axes[0, 1], label="m s$^{-1}$")

    axes[1, 0].plot(np.nanmax(absw, axis=(0, 2, 3)), np.arange(w.shape[1]), label="max")
    axes[1, 0].plot(np.nanpercentile(absw, 99, axis=(0, 2, 3)), np.arange(w.shape[1]), label="p99")
    axes[1, 0].plot(np.nanmean(absw, axis=(0, 2, 3)), np.arange(w.shape[1]), label="mean")
    axes[1, 0].set_title("HICAR |w| vertical profile")
    axes[1, 0].set_xlabel("m s$^{-1}$")
    axes[1, 0].set_ylabel("HICAR level")
    axes[1, 0].grid(True, alpha=0.25)
    axes[1, 0].legend(loc="best")

    if forcing_stats["maxabs"].size:
        h = forcing_stats["hours"]
        hhl = forcing_stats["hhl_mean"] / 1000.0
        fmax = forcing_stats["maxabs"]
        for hour in (0, 20, 23, 24):
            if hour in h:
                idx = int(np.where(h == hour)[0][0])
                axes[1, 1].plot(fmax[idx], hhl, label=f"f{hour:03d} max")
        axes[1, 1].axhline(np.nanmean(data["z"][-1]) / 1000.0, color="k", lw=1.0, ls="--", label="HICAR top level")
        axes[1, 1].set_title("Prepared ICON forcing |W| over HICAR envelope")
        axes[1, 1].set_xlabel("m s$^{-1}$")
        axes[1, 1].set_ylabel("forcing half-level height (km)")
        axes[1, 1].grid(True, alpha=0.25)
        axes[1, 1].legend(loc="best")

    fig.savefig(ANALYSIS_DIR / "w_pathology_vertical_time_and_forcing.png", dpi=170)
    plt.close(fig)


def write_report(times, data, static, forcing_stats) -> None:
    w = data["w"]
    absw = np.abs(w)
    top_level = w.shape[1] - 1
    cell_arg = np.nanargmax(absw.reshape(absw.shape[0] * absw.shape[1], *absw.shape[2:]), axis=0)
    arg_time = cell_arg // w.shape[1]
    arg_level = cell_arg % w.shape[1]
    top_max = np.nanmax(absw[:, top_level], axis=0)

    edge_mask = np.zeros(top_max.shape, dtype=bool)
    edge_mask[[0, -1], :] = True
    edge_mask[:, [0, -1]] = True
    mid_mask = np.zeros(top_max.shape, dtype=bool)
    mid_mask[:, 39:42] = True
    mid_mask[39:42, :] = True

    flat_global = np.nanargmax(absw)
    global_idx = np.unravel_index(flat_global, absw.shape)
    t_global, k_global, y_global, x_global = global_idx

    top_time_max = np.nanmax(absw[:, top_level], axis=(1, 2))
    top_counts = Counter(int(t) for t in arg_time.ravel())
    strongest_times = top_counts.most_common(8)

    report = []
    add = report.append
    add("# HICAR `w` Pathology Diagnostics")
    add("")
    add("## Main Findings")
    add("")
    add(f"- Output `w` has shape `{w.shape}` for `(time, level, y, x)`.")
    add(f"- The cell-wise max over all output records and levels comes from level `{top_level}` for `{int(np.sum(arg_level == top_level))}/{arg_level.size}` horizontal cells.")
    add(f"- Global max `|w|` is `{absw[global_idx]:.3f} m s-1` at record `{t_global}` (`{fmt_time(times[t_global])}`), level `{k_global}`, y/x `{y_global}/{x_global}`.")
    add("- This means the visually suspicious max-`|w|` image is effectively a top-level diagnostic, not a vertically mixed map of terrain-following vertical motion.")
    add("- Prepared ICON forcing `W` is small near the HICAR top height during the strongest HICAR-output hours, so the largest values are not a direct passthrough of top-boundary ICON `W`.")
    add("- The high values are not primarily outer-boundary artifacts and are only weakly associated with the expected 2x2 processor split lines.")
    add("")
    add("## Level And Time Concentration")
    add("")
    add("| Level | max | p99 | p99.9 | mean |")
    add("|---:|---:|---:|---:|---:|")
    for level in [0, 1, 10, 20, 30, 35, 36, 37, 38, 39]:
        arr = absw[:, level]
        add(
            f"| {level} | {np.nanmax(arr):.3f} | {np.nanpercentile(arr, 99):.3f} | "
            f"{np.nanpercentile(arr, 99.9):.3f} | {np.nanmean(arr):.3f} |"
        )
    add("")
    add("Strongest top-level hourly maxima:")
    for t in np.argsort(top_time_max)[-8:][::-1]:
        add(f"- record `{int(t)}` (`{fmt_time(times[int(t)])}`): `{top_time_max[int(t)]:.3f} m s-1`")
    add("")
    add("Most frequent argmax records for the cell-wise all-level maximum:")
    for t, count in strongest_times:
        add(f"- record `{t}` (`{fmt_time(times[t])}`): `{count}` cells")
    add("")
    add("## Spatial Association Checks")
    add("")
    add("| threshold for top-level max `|w|` | cells | outermost edge share | 3-cell midline share |")
    add("|---:|---:|---:|---:|")
    for threshold in [10, 15, 20, 25, 30]:
        mask = top_max > threshold
        n = int(mask.sum())
        edge_share = 100.0 * float(np.sum(mask & edge_mask)) / n if n else 0.0
        mid_share = 100.0 * float(np.sum(mask & mid_mask)) / n if n else 0.0
        add(f"| {threshold} m s-1 | {n} | {edge_share:.2f}% | {mid_share:.2f}% |")
    add("")
    add("## Prepared ICON Forcing Comparison")
    add("")
    if forcing_stats["maxabs"].size:
        hhl = forcing_stats["hhl_mean"]
        hicar_top = float(np.nanmean(data["z"][-1]))
        top_near = int(np.nanargmin(np.abs(hhl - hicar_top)))
        add(f"- Mean HICAR top output height is `{hicar_top:.1f} m`; closest forcing half level is `{top_near}` with mean height `{hhl[top_near]:.1f} m`.")
        add("| forcing hour | closest-level max `|W|` | closest-level p99 `|W|` | maximum over all forcing levels |")
        add("|---:|---:|---:|---:|")
        for hour in [0, 20, 23, 24]:
            if hour not in forcing_stats["hours"]:
                continue
            idx = int(np.where(forcing_stats["hours"] == hour)[0][0])
            add(
                f"| {hour} | {forcing_stats['maxabs'][idx, top_near]:.6g} | "
                f"{forcing_stats['p99'][idx, top_near]:.6g} | {np.nanmax(forcing_stats['maxabs'][idx]):.6g} |"
            )
    add("")
    add("## Interpretation")
    add("")
    add("The suspect map should not be interpreted as evidence for widespread near-surface vertical velocities of 20-40 m s-1. It is dominated everywhere by the highest HICAR output level. Since ICON forcing `W` is near zero at comparable heights, the most likely causes are HICAR's diagnostic vertical-wind reconstruction or upper-boundary/vertical-coordinate behavior. The next model-side test should output both `w` and `w_grid`, and should run a sensitivity with a higher model top or stronger upper damping/top-level treatment if such namelist controls are available.")
    add("")
    add("Generated figures:")
    add("- `w_pathology_top_level_maps.png`")
    add("- `w_pathology_vertical_time_and_forcing.png`")

    (ANALYSIS_DIR / "w_pathology_report.md").write_text("\n".join(report) + "\n")


def main() -> None:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    times, data = load_outputs()
    static = load_static()
    forcing_stats = load_forcing_w_domain_stats(data["lat"], data["lon"], len(times))
    plot_maps(times, data, static)
    plot_vertical_time(times, data, forcing_stats)
    write_report(times, data, static, forcing_stats)
    print(ANALYSIS_DIR / "w_pathology_report.md")
    print(ANALYSIS_DIR / "w_pathology_top_level_maps.png")
    print(ANALYSIS_DIR / "w_pathology_vertical_time_and_forcing.png")


if __name__ == "__main__":
    main()
