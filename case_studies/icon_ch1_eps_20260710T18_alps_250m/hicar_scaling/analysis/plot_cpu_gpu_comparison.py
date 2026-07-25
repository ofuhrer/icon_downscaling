#!/usr/bin/env python3
"""Create timestep-normalized CPU and A100 GPU scaling figures."""
import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt


STYLE = {
    "cpu": {"color": "#2166ac", "marker": "o", "label": "AMD EPYC 7713 CPU sockets"},
    "gpu": {"color": "#b35806", "marker": "s", "label": "NVIDIA A100 96GB GPU sockets"},
}
COMPONENTS = {
    "microphysics": ("#7b3294", "microphysics"),
    "advection": ("#b35806", "advection"),
    "halo": ("#008837", "halo wait + retrieve"),
}


def read(path: Path):
    return json.loads(path.read_text())


CPU_COMPUTE_RANKS_PER_SOCKET = 32


def resource_sockets(compute_ranks, platform):
    """Convert compute ranks (already excluding I/O) to hardware sockets.

    CPU jobs place 64 compute ranks and one I/O rank on a dual-socket EPYC
    node, hence 32 compute ranks per physical socket.  GPU jobs use one
    compute rank per A100 and one CPU-only I/O rank per node.
    """
    return compute_ranks / CPU_COMPUTE_RANKS_PER_SOCKET if platform == "cpu" else compute_ranks


def timestep_summary(root: Path, platform: str):
    """Aggregate critical-path timestep rate and normalized efficiency.

    Six-hour cases can take different adaptive timesteps as their domains
    change.  Use the effective step count inferred by the timer parser rather
    than simulated-duration throughput, so every plotted sample is normalized
    to one integration timestep.
    """
    groups = defaultdict(list)
    for row in read(root / "analysis" / "scaling_runs.json"):
        steps = row.get("estimated_integration_steps")
        physics = row.get("physics_max_s")
        if row["platform"] == platform and steps and physics:
            groups[(row["kind"], row["width_km"], row["height_km"], row["compute_ranks"])].append(row)
    summary = []
    for (kind, width, height, ranks), rows in groups.items():
        rates = [row["estimated_integration_steps"] / row["physics_max_s"] for row in rows]
        summary.append({"platform": platform, "kind": kind, "width_km": width, "height_km": height,
                        "compute_ranks": ranks, "repeats": len(rates),
                        "timesteps_per_second": statistics.median(rates),
                        "timesteps_per_second_min": min(rates),
                        "timesteps_per_second_max": max(rates)})
    for row in summary:
        candidates = [item for item in summary if item["kind"] == row["kind"]]
        if row["kind"] == "strong":
            candidates = [item for item in candidates if (item["width_km"], item["height_km"]) ==
                          (row["width_km"], row["height_km"])]
        baseline = min(candidates, key=lambda item: item["compute_ranks"])
        row["baseline_compute_ranks"] = baseline["compute_ranks"]
        row["speedup"] = row["timesteps_per_second"] / baseline["timesteps_per_second"]
        row["parallel_efficiency"] = (row["speedup"] if row["kind"] == "weak" else
                                      row["speedup"] / (row["compute_ranks"] / baseline["compute_ranks"]))
    return summary


def draw_curve(ax, rows, platform, metric, *, domain=None, kind="strong", title="", log_y=False):
    values = [row for row in rows if row["kind"] == kind and (domain is None or
              (row["width_km"], row["height_km"]) == domain)]
    if not values:
        return False
    values.sort(key=lambda row: row["compute_ranks"])
    x = [resource_sockets(row["compute_ranks"], platform) for row in values]
    y = [row[metric] for row in values]
    style = STYLE[platform]
    ax.plot(x, y, marker=style["marker"], color=style["color"], linewidth=2,
            markersize=5, label=style["label"])
    if metric == "timesteps_per_second":
        low = [row[metric] - row["timesteps_per_second_min"] for row in values]
        high = [row["timesteps_per_second_max"] - row[metric] for row in values]
        ax.errorbar(x, y, yerr=[low, high], color=style["color"], capsize=3, linewidth=0)
    ax.set_xscale("log", base=2)
    if log_y:
        ax.set_yscale("log")
    ax.set_title(title, loc="left", fontweight="bold")
    ax.grid(axis="y", color="#d9d9d9", linewidth=0.8)
    return True


def scaling_figure(cpu, gpu, out):
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    panels = [
        ((80, 80), "strong", "Strong scaling: 80×80 km"),
        ((240, 160), "strong", "Strong scaling: 240×160 km"),
        (None, "weak", "Weak scaling"),
    ]
    for col, (domain, kind, title) in enumerate(panels):
        for platform, rows in (("cpu", cpu), ("gpu", gpu)):
            draw_curve(axes[0, col], rows, platform, "timesteps_per_second", domain=domain,
                       kind=kind, title=title, log_y=True)
            draw_curve(axes[1, col], rows, platform, "parallel_efficiency", domain=domain,
                       kind=kind, title="", log_y=False)
        axes[0, col].set_ylabel("timesteps / critical-path second")
        axes[1, col].set_ylabel("timestep-normalized efficiency")
        axes[1, col].set_xlabel("# sockets (AMD EPYC 7713 or NVIDIA A100 96GB)")
        axes[1, col].set_ylim(0, 1.05)
    axes[0, 1].annotate("CPU p1–p32 were excluded: projected\nruntime is above the 4 h benchmark limit.",
                        xy=(0.04, 0.05), xycoords="axes fraction", fontsize=8,
                        bbox={"boxstyle": "round,pad=0.3", "fc": "#fff7e6", "ec": "#b35806"})
    axes[0, 2].annotate("CPU tiles are 10×10 km/rank; GPU tiles are 40×40 km/GPU.\nEach point is normalized by its measured timestep count.",
                        xy=(0.04, 0.04), xycoords="axes fraction", fontsize=8,
                        bbox={"boxstyle": "round,pad=0.3", "fc": "#f7f7f7", "ec": "#777777"})
    axes[0, 0].annotate("CPU: (MPI ranks − I/O ranks) / 32 per socket.\nGPU: MPI compute ranks = A100 GPU sockets.",
                        xy=(0.04, 0.05), xycoords="axes fraction", fontsize=8,
                        bbox={"boxstyle": "round,pad=0.3", "fc": "#f7f7f7", "ec": "#777777"})
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=2, frameon=False, bbox_to_anchor=(0.5, 1.02))
    fig.suptitle("HICAR 250 m production configuration: timestep-normalized CPU and A100 GPU scaling", fontsize=14, fontweight="bold")
    fig.savefig(out / "cpu_gpu_timestep_normalized_scaling.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def component_figure(cpu_root, gpu_root, out):
    source = {"cpu": read(cpu_root / "analysis" / "scaling_runs.json"),
              "gpu": read(gpu_root / "analysis" / "scaling_runs.json")}
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), constrained_layout=True)
    for ax, platform in zip(axes, ("cpu", "gpu")):
        grouped = defaultdict(list)
        for row in source[platform]:
            if (row["platform"] == platform and row["kind"] == "strong" and
                    (row["width_km"], row["height_km"]) == (80, 80) and row["physics_max_s"]):
                grouped[row["compute_ranks"]].append(row)
        for key, (color, label) in COMPONENTS.items():
            x, y = [], []
            for ranks in sorted(grouped):
                samples = []
                for row in grouped[ranks]:
                    timers = row["timers"]
                    if key == "halo":
                        value = timers.get("halo-exchange(wait)", {}).get("max_s", 0) + timers.get("halo-exchange(retrieve)", {}).get("max_s", 0)
                    else:
                        value = timers.get(key, {}).get("max_s")
                    if value is not None and row.get("estimated_integration_steps"):
                        samples.append(1000.0 * value / row["estimated_integration_steps"])
                if samples:
                    x.append(resource_sockets(ranks, platform)); y.append(statistics.median(samples))
            if x:
                ax.plot(x, y, marker="o" if platform == "cpu" else "s", color=color,
                        linewidth=2, markersize=5, label=label)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xlabel("# AMD EPYC 7713 CPU sockets\n(32 compute ranks/socket)" if platform == "cpu" else "# NVIDIA A100 96GB GPU sockets")
        ax.set_ylabel("max-rank component time (ms / timestep)")
        ax.set_title(f"{platform.upper()} strong scaling: 80×80 km", loc="left", fontweight="bold")
        ax.grid(axis="y", color="#d9d9d9", linewidth=0.8)
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle("Critical-path component time per timestep (non-additive HICAR timers)", fontsize=13, fontweight="bold")
    fig.savefig(out / "cpu_gpu_timestep_normalized_components.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-root", type=Path, required=True)
    parser.add_argument("--gpu-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    scaling_figure(timestep_summary(args.cpu_root, "cpu"), timestep_summary(args.gpu_root, "gpu"), args.out)
    component_figure(args.cpu_root, args.gpu_root, args.out)


if __name__ == "__main__":
    main()
