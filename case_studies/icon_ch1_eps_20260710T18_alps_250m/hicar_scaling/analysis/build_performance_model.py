#!/usr/bin/env python3
"""Fit and use a deliberately small, calibrated HICAR throughput model.

The model predicts *timed integration* seconds per timestep.  It separates
the useful grid-cell work assigned to a compute socket from the parallel
overhead which grows with socket count::

    log(t_step) = c0 + c_work*log(cells/socket) + c_socket*log(sockets)
                  + c_interaction*log(cells/socket)*log(sockets)

It is an empirical interpolation of this case study, rather than a hardware
or memory-capacity model.  CPU ``sockets`` mean 32 compute ranks (the
benchmark's rank placement); GPU sockets mean A100 devices.
"""
import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


CPU_RANKS_PER_SOCKET = 32
CONFIGURATION = "HICAR production configuration, 80 levels, 250 m benchmark forcing"


def read(path):
    return json.loads(Path(path).read_text())


def sockets(row):
    return row["compute_ranks"] / CPU_RANKS_PER_SOCKET if row["platform"] == "cpu" else row["compute_ranks"]


def groups(cpu_path, gpu_path):
    """Return median repetitions, one row per scenario/resource point."""
    aggregate = defaultdict(list)
    for path, platform in ((cpu_path, "cpu"), (gpu_path, "gpu")):
        for row in read(path):
            if row.get("platform") != platform:
                continue
            steps, elapsed = row.get("estimated_integration_steps"), row.get("physics_max_s")
            if not steps or not elapsed:
                continue
            key = (platform, row["kind"], row["width_km"], row["height_km"], row["compute_ranks"])
            aggregate[key].append(row)
    result = []
    for (platform, kind, width, height, ranks), rows in aggregate.items():
        times = [r["physics_max_s"] / r["estimated_integration_steps"] for r in rows]
        dts = [r["effective_timestep_s"] for r in rows if r.get("effective_timestep_s")]
        resource_count = ranks / CPU_RANKS_PER_SOCKET if platform == "cpu" else ranks
        result.append({
            "platform": platform, "kind": kind, "width_km": width, "height_km": height,
            "compute_ranks": ranks, "sockets": resource_count, "repeats": len(times),
            "cells": width * height * 1_000_000 / 250**2,
            "cells_per_socket": width * height * 1_000_000 / 250**2 / resource_count,
            "seconds_per_timestep": statistics.median(times),
            "effective_timestep_s": statistics.median(dts) if dts else None,
        })
    return sorted(result, key=lambda r: (r["platform"], r["kind"], r["cells"], r["sockets"]))


def model(x, p):
    """Working-set-aware log-linear interpolation, fitted for relative error."""
    cells_per_socket, resource_count = x
    log_cells = np.log(cells_per_socket)
    log_sockets = np.log(resource_count)
    c0, c_work, c_socket, c_interaction = p
    return np.exp(c0 + c_work * log_cells + c_socket * log_sockets +
                  c_interaction * log_cells * log_sockets)


def fit(rows):
    x = np.array([[r["cells_per_socket"], r["sockets"]] for r in rows]).T
    y = np.array([r["seconds_per_timestep"] for r in rows])
    log_cells, log_sockets = np.log(x[0]), np.log(x[1])
    design = np.column_stack((np.ones(len(y)), log_cells, log_sockets,
                              log_cells * log_sockets))
    coefficients = np.linalg.lstsq(design, np.log(y), rcond=None)[0]
    predicted = model(x, coefficients)
    ape = np.abs(predicted / y - 1.0)
    return coefficients, predicted, {
        "median_absolute_percentage_error": float(np.median(ape) * 100),
        "max_absolute_percentage_error": float(np.max(ape) * 100),
        "rmse_seconds_per_timestep": float(np.sqrt(np.mean((predicted - y)**2))),
    }


def nearest_timestep(rows, platform, width, height):
    options = [r for r in rows if r["platform"] == platform and r["effective_timestep_s"]]
    if not options:
        return None, None
    nearest = min(options, key=lambda r: abs(math.log((r["width_km"] * r["height_km"]) / (width * height))))
    return nearest["effective_timestep_s"], nearest


def build_artifacts(rows, out):
    out.mkdir(parents=True, exist_ok=True)
    fits, payload = {}, {"model_version": 1, "configuration": CONFIGURATION,
                         "resolution_reference_m": 250,
                         "formula": "log(t_step_s) = c0 + c_work*log(cells_per_socket) + c_socket*log(sockets) + c_interaction*log(cells_per_socket)*log(sockets)",
                         "definitions": {"cpu_socket": "32 HICAR compute MPI ranks", "gpu_socket": "one NVIDIA A100 96GB GPU"},
                         "limitations": [
                             "Timed integration only: initialization, I/O, queueing, and staging are excluded.",
                             "Calibrated only to the listed 250 m, 80-level production configuration and ICON forcing window.",
                             "Resolution changes scale grid-cell count exactly but timestep only by an assumed CFL-linear dx relation unless supplied explicitly.",
                             "Legacy measurements contain no reliable memory high-water marks; this model does not predict capacity or memory.",
                         ], "platforms": {}}
    for platform in ("cpu", "gpu"):
        subset = [r for r in rows if r["platform"] == platform]
        params, predicted, quality = fit(subset)
        names = ["c0", "c_work", "c_socket", "c_interaction"]
        payload["platforms"][platform] = {
            "parameters": {k: float(v) for k, v in zip(names, params)}, "fit_quality": quality,
            "calibration_range": {
                "sockets": [min(r["sockets"] for r in subset), max(r["sockets"] for r in subset)],
                "cells_per_socket": [min(r["cells_per_socket"] for r in subset), max(r["cells_per_socket"] for r in subset)],
            },
        }
        fits[platform] = (subset, params, predicted)
    (out / "performance_model.json").write_text(json.dumps(payload, indent=2) + "\n")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    colors = {"strong": "#2166ac", "weak": "#b35806"}
    for ax, platform in zip(axes, ("cpu", "gpu")):
        subset, params, predicted = fits[platform]
        observed = np.array([r["seconds_per_timestep"] for r in subset])
        for kind in ("strong", "weak"):
            ix = [i for i, r in enumerate(subset) if r["kind"] == kind]
            ax.scatter(observed[ix] * 1000, predicted[ix] * 1000, label=kind,
                       color=colors[kind], s=42, alpha=0.9)
        lo, hi = min(observed.min(), predicted.min()) * 800, max(observed.max(), predicted.max()) * 1250
        ax.plot([lo, hi], [lo, hi], color="#555555", lw=1, ls="--")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
        ax.set_xlabel("measured critical-path time (ms / timestep)")
        ax.set_ylabel("model estimate (ms / timestep)")
        ax.set_title("CPU" if platform == "cpu" else "NVIDIA A100 96GB GPU", loc="left", fontweight="bold")
        ax.grid(which="both", alpha=0.25)
        ax.legend(frameon=False)
    fig.suptitle("Empirical timed-phase model: measured versus fitted", fontweight="bold")
    fig.savefig(out / "performance_model_fit.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    lines = ["# HICAR benchmark performance model", "", f"Calibrated to: {CONFIGURATION}.", "",
             "The model estimates timed integration wall time. CPU socket-equivalents are 32 compute MPI ranks; a GPU socket is one A100 96GB.",
             "", "`log(t_step) = c0 + c_work×log(cells/socket) + c_socket×log(sockets) + c_interaction×log(cells/socket)×log(sockets)`", "",
             "| Platform | median absolute error | calibration sockets | cells/socket |", "|---|---:|---:|---:|"]
    for platform, item in payload["platforms"].items():
        q, c = item["fit_quality"], item["calibration_range"]
        lines.append(f"| {platform} | {q['median_absolute_percentage_error']:.1f}% | {c['sockets'][0]:g}–{c['sockets'][1]:g} | {c['cells_per_socket'][0]:.0f}–{c['cells_per_socket'][1]:.0f} |")
    lines += ["", "## Use", "", "Run this script with `--estimate ...`. Supply the production timestep when known. Without it, the script selects the nearest measured domain and scales its timestep linearly with resolution; that is a CFL assumption, not a forecast.", "", "## Guardrails", ""]
    lines += [f"- {item}" for item in payload["limitations"]]
    lines += ["- Estimates outside the calibrated socket or working-set range are explicitly marked extrapolated.",
              "- The new benchmark launcher records per-rank CPU RSS and GPU memory peak for future memory fits; historical runs have no usable Slurm memory values."]
    (out / "performance_model_report.md").write_text("\n".join(lines) + "\n")
    return payload


def estimate(model_data, rows, args):
    platform = args.platform
    socket_count = args.sockets
    cells = args.width_km * args.height_km * 1_000_000 / args.resolution_m**2
    cell_per_socket = cells / socket_count
    parameters = model_data["platforms"][platform]["parameters"]
    p = [parameters[key] for key in ("c0", "c_work", "c_socket", "c_interaction")]
    step_s = float(model(np.array([[cell_per_socket], [socket_count]]), p)[0])
    if args.timestep_s:
        timestep = args.timestep_s; timestep_source = "user supplied"
    else:
        reference_dt, reference = nearest_timestep(rows, platform, args.width_km, args.height_km)
        timestep = reference_dt * args.resolution_m / 250.0
        timestep_source = f"nearest measured {reference['width_km']}x{reference['height_km']} km domain, CFL-scaled from {reference_dt:.3g} s"
    run_s = step_s * args.hours * 3600 / timestep
    bounds = model_data["platforms"][platform]["calibration_range"]
    extrapolated = not (bounds["sockets"][0] <= socket_count <= bounds["sockets"][1] and
                         bounds["cells_per_socket"][0] <= cell_per_socket <= bounds["cells_per_socket"][1])
    typical_error = model_data["platforms"][platform]["fit_quality"]["median_absolute_percentage_error"] / 100
    print(json.dumps({"platform": platform, "domain_km": [args.width_km, args.height_km],
                      "resolution_m": args.resolution_m, "sockets": socket_count,
                      "cells_per_socket": cell_per_socket, "simulated_hours": args.hours,
                      "timestep_s": timestep, "timestep_source": timestep_source,
                      "timed_seconds_per_timestep": step_s, "estimated_timed_wall_seconds": run_s,
                      "estimated_timed_wall_hours": run_s / 3600,
                      "typical_in_sample_error_fraction": typical_error,
                      "typical_timed_wall_hours_range": [run_s / 3600 * (1 - typical_error),
                                                           run_s / 3600 * (1 + typical_error)],
                      "extrapolated": extrapolated,
                      "memory_prediction": "unavailable: no historical high-water measurements"}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-runs", type=Path, required=True)
    parser.add_argument("--gpu-runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("--platform", choices=("cpu", "gpu"))
    parser.add_argument("--width-km", type=float)
    parser.add_argument("--height-km", type=float)
    parser.add_argument("--resolution-m", type=float, default=250)
    parser.add_argument("--sockets", type=float)
    parser.add_argument("--hours", type=float, default=6)
    parser.add_argument("--timestep-s", type=float)
    args = parser.parse_args()
    rows = groups(args.cpu_runs, args.gpu_runs)
    data = build_artifacts(rows, args.out)
    if args.estimate:
        missing = [name for name in ("platform", "width_km", "height_km", "sockets") if getattr(args, name) is None]
        if missing:
            parser.error("--estimate requires " + ", ".join("--" + name.replace("_", "-") for name in missing))
        estimate(data, rows, args)


if __name__ == "__main__":
    main()
