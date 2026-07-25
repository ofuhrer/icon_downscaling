#!/usr/bin/env python3
"""Parse HICAR timer summaries and produce CSV/JSON/Markdown scaling reports."""
import argparse, csv, json, re, statistics, subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional


def plot_scaling_guides(summary, out):
    """Create decision-oriented figures from validated repeat medians only."""
    import matplotlib.pyplot as plt

    colors = {"80x80 km": "#2166ac", "240x160 km": "#b35806"}
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})

    def curves(kind):
        grouped = defaultdict(list)
        for item in summary:
            if item["platform"] != "gpu" or item["kind"] != kind:
                continue
            grouped[(item["width_km"], item["height_km"])].append(item)
        return grouped

    def draw_metric(ax, kind, metric, ylabel, title, efficiency=False):
        if kind == "weak":
            values = sorted((x for x in summary if x["platform"] == "gpu" and x["kind"] == "weak"),
                            key=lambda x: x["compute_ranks"])
            ranks = [x["compute_ranks"] for x in values]
            value = [x[metric] for x in values]
            if efficiency:
                ax.plot(ranks, value, "o-", color="#555555", label="40x40 km per GPU tile")
            else:
                low = [x["physics_sdpd_median"] - x["physics_sdpd_min"] for x in values]
                high = [x["physics_sdpd_max"] - x["physics_sdpd_median"] for x in values]
                ax.errorbar(ranks, value, yerr=[low, high], fmt="o-", capsize=3,
                            color="#555555", label="40x40 km per GPU tile")
            for index, item in enumerate(values):
                offset = 8 if index % 2 == 0 else -18
                ax.annotate("%dx%d" % (item["width_km"], item["height_km"]),
                            (item["compute_ranks"], item[metric]), xytext=(0, offset),
                            textcoords="offset points", ha="center", fontsize=7)
            ax.set_xscale("log", base=2)
            ax.set_xlabel("compute GPUs")
            ax.set_ylabel(ylabel)
            ax.set_title(title, loc="left", fontweight="bold")
            ax.grid(axis="y", color="#d9d9d9", linewidth=0.8)
            ax.legend(frameon=False, fontsize=8)
            return
        for domain, values in sorted(curves(kind).items()):
            values.sort(key=lambda x: x["compute_ranks"])
            ranks = [x["compute_ranks"] for x in values]
            value = [x[metric] for x in values]
            label = f"{domain[0]}x{domain[1]} km"
            key = label.replace("x", "x")
            color = colors.get(label, "#555555")
            if efficiency:
                ax.plot(ranks, value, "o-", color=color, label=label)
            else:
                low = [x["physics_sdpd_median"] - x["physics_sdpd_min"] for x in values]
                high = [x["physics_sdpd_max"] - x["physics_sdpd_median"] for x in values]
                ax.errorbar(ranks, value, yerr=[low, high], fmt="o-", capsize=3, color=color, label=label)
            for item in values:
                if not item["one_resource_baseline_available"] and item["compute_ranks"] == item["baseline_compute_ranks"]:
                    ax.annotate("smallest valid\npoint", (item["compute_ranks"], item[metric]),
                                xytext=(6, -28), textcoords="offset points", fontsize=8, color=color)
        ax.set_xscale("log", base=2)
        ax.set_xlabel("compute GPUs")
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontweight="bold")
        ax.grid(axis="y", color="#d9d9d9", linewidth=0.8)
        ax.legend(frameon=False, fontsize=8)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    draw_metric(axes[0, 0], "strong", "physics_sdpd_median", "timed-phase SDPD", "GPU strong scaling: throughput")
    axes[0, 0].annotate("1-2 GPUs are not supported for 240x160 km\n(A100 RRTMGP memory capacity)",
                        xy=(0.04, 0.06), xycoords="axes fraction", fontsize=8,
                        bbox={"boxstyle": "round,pad=0.35", "fc": "#fff7e6", "ec": "#b35806"})
    draw_metric(axes[0, 1], "strong", "parallel_efficiency", "parallel efficiency", "GPU strong scaling: efficiency", efficiency=True)
    axes[0, 1].set_ylim(0, 1.05)

    draw_metric(axes[1, 0], "weak", "physics_sdpd_median", "timed-phase SDPD", "GPU weak scaling: throughput")
    draw_metric(axes[1, 1], "weak", "parallel_efficiency", "weak-scaling efficiency", "GPU weak scaling: efficiency", efficiency=True)
    axes[1, 1].set_ylim(0, 1.05)
    axes[1, 0].annotate("Tile: 40x40 km per GPU.  Error bars show repeat min-max.",
                        xy=(0.04, 0.06), xycoords="axes fraction", fontsize=8,
                        bbox={"boxstyle": "round,pad=0.35", "fc": "#f7f7f7", "ec": "#777777"})

    fig.suptitle("HICAR 250 m production configuration: validated GPU scaling", fontsize=14, fontweight="bold")
    fig.savefig(out / "gpu_scaling_guide.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    guide = [
        "# Throughput selection guide",
        "",
        "Timed-phase SDPD is based on the maximum compute-rank physics timer; ranges are repeat minima/maxima.",
        "",
        "## GPU strong scaling",
        "",
        "| domain | GPUs | median SDPD | repeat range | efficiency | interpretation |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in sorted((x for x in summary if x["platform"] == "gpu" and x["kind"] == "strong"), key=lambda x: (x["width_km"] * x["height_km"], x["compute_ranks"])):
        note = "relative to p%d" % item["baseline_compute_ranks"] if not item["one_resource_baseline_available"] else "relative to p1"
        guide.append("| %dx%d km | %d | %.1f | %.1f-%.1f | %.0f%% | %s |" % (
            item["width_km"], item["height_km"], item["compute_ranks"], item["physics_sdpd_median"],
            item["physics_sdpd_min"], item["physics_sdpd_max"], 100 * item["parallel_efficiency"], note))
    guide += ["", "## Important limits", "",
              "- 240x160 km requires at least 12 A100 GPUs in this configuration; p1/p2 exhaust device memory.",
              "- CPU: only the 10x10 km p1 weak point (204.6 timed-phase SDPD) is currently comparable. Re-run CPU scaling after the MPI/I/O fix; do not extrapolate it.",
              "- The p20 80x80 km strong point has one low repeat; use the median and shown range rather than a single run."]
    (out / "throughput_selection_guide.md").write_text("\n".join(guide) + "\n")


def plot_timer_components(rows, out):
    """Plot max-rank component timing; subcomponent lines are not additive."""
    import matplotlib.pyplot as plt

    component_style = {
        "physics": ("#1b4f72", "physics (critical path)"),
        "init": ("#7f8c8d", "initialization"),
        "advection": ("#b35806", "advection"),
        "microphysics": ("#7b3294", "microphysics"),
        "halo-exchange(retrieve)": ("#008837", "halo retrieve"),
        "halo-exchange(wait)": ("#e08214", "halo wait"),
    }

    def aggregate(kind, domain):
        grouped = defaultdict(list)
        for row in rows:
            if row["platform"] == "gpu" and row["kind"] == kind and (domain is None or (row["width_km"], row["height_km"]) == domain):
                grouped[row["compute_ranks"]].append(row)
        return [(rank, grouped[rank]) for rank in sorted(grouped)]

    def series(groups, component, per_step=False):
        values = []
        for rank, runs in groups:
            samples = []
            for run in runs:
                value = run["timers"].get(component, {}).get("max_s")
                if value is not None:
                    samples.append(value / run["estimated_integration_steps"] if per_step and run.get("estimated_integration_steps") else value)
            values.append(statistics.median(samples) if samples else None)
        return values

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), constrained_layout=True)
    panels = [
        (axes[0], aggregate("strong", (80, 80)), "80x80 km strong scaling", "seconds per six-hour run", False),
        (axes[1], aggregate("strong", (240, 160)), "240x160 km strong scaling", "seconds per six-hour run", False),
        (axes[2], aggregate("weak", None), "Weak scaling, normalized per timestep", "max-rank seconds per timestep", True),
    ]
    for ax, groups, title, ylabel, per_step in panels:
        ranks = [rank for rank, _ in groups]
        for component, (color, label) in component_style.items():
            values = series(groups, component, per_step)
            if any(value is not None for value in values):
                ax.plot(ranks, values, "o-", color=color, label=label)
        ax.set_xscale("log", base=2)
        ax.set_xlabel("compute GPUs")
        ax.set_ylabel(ylabel)
        ax.set_title(title, loc="left", fontweight="bold")
        ax.grid(axis="y", color="#d9d9d9", linewidth=0.8)
    axes[0].legend(frameon=False, fontsize=8)
    axes[2].annotate("Weak domains have different adaptive timesteps;\nthis panel normalizes by estimated step count.",
                     xy=(0.04, 0.05), xycoords="axes fraction", fontsize=8,
                     bbox={"boxstyle": "round,pad=0.3", "fc": "#f7f7f7", "ec": "#777777"})
    fig.suptitle("HICAR GPU timer scaling (max compute-rank time; component lines are non-additive)", fontweight="bold")
    fig.savefig(out / "gpu_timer_component_scaling.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

TIMER = re.compile(r"^\s*(.+?)\s*:\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*$", re.M)
TIMESTEP = re.compile(r"^\s*time_step:\s*([0-9.]+)\s+seconds\s*$", re.M)
PROGRESS_TIMESTEP = re.compile(r"^\s*[0-9.]+\s*%\s+dt=\s*([0-9.]+)\s+seconds\s*$", re.M)
SLURM = re.compile(r"ElapsedRaw=(\d+)")
JOB_ID = re.compile(r"\bJobId=(\d+)")
ARRAY_ID = re.compile(r"\bArrayJobId=(\d+)\s+ArrayTaskId=(\d+)")
MAX_RSS = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")
GPU_PEAK_MIB = re.compile(r"peak_gpu_memory_mib=(\d+)")

def outer_slurm_log(job_text: str) -> Optional[Path]:
    """Locate the array wrapper log retained by Slurm, when running on Balfrin."""
    match = ARRAY_ID.search(job_text)
    if not match:
        return None
    path = Path("/users") / Path.home().name / f"slurm-{match.group(1)}_{match.group(2)}.out"
    return path if path.is_file() else None

def slurm_elapsed(job_text: str) -> Optional[int]:
    """Return the completed Slurm allocation duration when sacct is available."""
    # An array parent can have several completed tasks.  Querying its bare ID
    # returns an arbitrary task's elapsed time, so retain the task ID recorded
    # by Slurm for this specific repeat.  The provenance can contain stale
    # ElapsedRaw fields from other array tasks, so do this before cached data.
    array = ARRAY_ID.search(job_text)
    if array:
        job_id = f"{array.group(1)}_{array.group(2)}"
    else:
        cached = SLURM.findall(job_text)
        if cached:
            return int(cached[-1])
        match = JOB_ID.search(job_text)
        if not match:
            return None
        job_id = match.group(1)
    try:
        result = subprocess.run(
            ["sacct", "-X", "-n", "-P", "-j", job_id, "--format=ElapsedRaw"],
            universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError:
        return None
    for line in result.stdout.splitlines():
        value = line.split("|", 1)[0].strip()
        if value.isdigit():
            return int(value)
    return None
def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--root",type=Path,required=True); a=p.parse_args(); root=a.root
    manifest=json.loads((root/"manifest.json").read_text()); by_id={s["id"]:s for s in manifest["scenarios"]}; rows=[]; failures=[]
    for run in (root/"runs").glob("*/repeat_*"):
        scenario=by_id[run.parent.name]
        logs=list((run/"logs").glob("hicar_*.out"))+list((run/"logs").glob("slurm_*.out"))
        if not (run/"VALIDATED").exists():
            job_text=(run / "provenance_slurm.txt").read_text(errors="replace") if (run / "provenance_slurm.txt").is_file() else ""
            outer=outer_slurm_log(job_text)
            text="\n".join(x.read_text(errors="replace") for x in logs)
            if outer:
                text += "\n" + outer.read_text(errors="replace")
            if re.search(r"CUDA_ERROR_OUT_OF_MEMORY|Out of memory", text, re.I): reason="gpu_memory_capacity"
            elif re.search(r"Negative count|MPI_Irecv.*Invalid count", text, re.I): reason="mpi_count_limit"
            elif re.search(r"Updating Boundary conditions.*PMPI_Waitall|PMPI_Waitall.*supplied request.*invalid", text, re.I | re.S): reason="mpi_halo_waitall"
            elif text: reason="unvalidated_or_failed"
            else: reason="not_started"
            failures.append({**scenario,"repeat":run.name,"reason":reason})
            continue
        timer_logs=[x for x in logs if "Timing across all compute images:" in x.read_text(errors="replace")]
        log=max(timer_logs,key=lambda x:x.stat().st_mtime) if timer_logs else None
        if not log: continue
        log_text = log.read_text(errors="replace")
        timers={name.strip(): {"mean_s":float(mean),"min_s":float(min_),"max_s":float(max_)} for name,mean,min_,max_ in TIMER.findall(log_text)}
        timestep_matches = TIMESTEP.findall(log_text)
        initial_timestep_s = float(timestep_matches[0]) if timestep_matches else None
        progress_timesteps = [float(value) for value in PROGRESS_TIMESTEP.findall(log_text)]
        # Progress is logged in equal simulated-time increments.  The harmonic
        # mean therefore estimates the timestep relevant to total step count.
        effective_timestep_s = (len(progress_timesteps) / sum(1.0 / value for value in progress_timesteps)
                                if progress_timesteps else None)
        job_text="\n".join(f.read_text(errors="replace") for f in (run.glob("provenance_slurm.txt")))
        elapsed=slurm_elapsed(job_text)
        physics=timers.get("physics",{}).get("max_s")
        memory_dir=run / "logs" / "memory"
        cpu_rss=[int(m.group(1)) for path in memory_dir.glob("cpu_rank_*.time")
                 for m in [MAX_RSS.search(path.read_text(errors="replace"))] if m]
        gpu_peak=[int(m.group(1)) for path in memory_dir.glob("gpu_rank_*.txt")
                  for m in [GPU_PEAK_MIB.search(path.read_text(errors="replace"))] if m]
        # SDPD is simulated days per 24 hours of wall time.  The raw ratio
        # (simulated seconds / wall seconds) is dimensionless and needs the
        # factor of 24 to express the requested days-per-day unit.
        row={**scenario,"repeat":run.name,"slurm_elapsed_s":elapsed,"physics_max_s":physics,"job_sdpd":24*21600/elapsed if elapsed else None,"physics_sdpd":24*21600/physics if physics else None,"initial_timestep_s":initial_timestep_s,"effective_timestep_s":effective_timestep_s,"estimated_integration_steps":21600/effective_timestep_s if effective_timestep_s else None,"cpu_rank_rss_kib_max":max(cpu_rss) if cpu_rss else None,"cpu_rank_rss_kib_mean":statistics.mean(cpu_rss) if cpu_rss else None,"cpu_memory_rank_samples":len(cpu_rss),"gpu_rank_peak_mib_max":max(gpu_peak) if gpu_peak else None,"gpu_rank_peak_mib_mean":statistics.mean(gpu_peak) if gpu_peak else None,"gpu_memory_rank_samples":len(gpu_peak),"timers":timers}
        rows.append(row)
    out=root/"analysis"; out.mkdir(exist_ok=True)
    (out/"scaling_runs.json").write_text(json.dumps(rows,indent=2)+"\n")
    (out/"scaling_failures.json").write_text(json.dumps(failures,indent=2)+"\n")
    flat=[]
    for r in rows: flat.append({k:v for k,v in r.items() if k!="timers"})
    with (out/"scaling_runs.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=flat[0].keys() if flat else ["id"]); w.writeheader(); w.writerows(flat)
    groups=defaultdict(list)
    for r in rows: groups[(r["platform"],r["kind"],r["width_km"],r["height_km"],r["compute_ranks"])].append(r)
    summary=[]
    for key,vals in sorted(groups.items()):
        metric=[v["physics_sdpd"] for v in vals if v["physics_sdpd"]]
        if metric: summary.append({"platform":key[0],"kind":key[1],"width_km":key[2],"height_km":key[3],"compute_ranks":key[4],"repeats":len(metric),"physics_sdpd_median":statistics.median(metric),"physics_sdpd_min":min(metric),"physics_sdpd_max":max(metric)})
    # Strong-scaling baselines share a platform and fixed domain.  Weak curves
    # are normalized to their platform's one-resource tile.
    for s in summary:
        candidates=[x for x in summary if x["platform"]==s["platform"] and x["kind"]==s["kind"]]
        if s["kind"] == "strong":
            candidates=[x for x in candidates if x["width_km"]==s["width_km"] and x["height_km"]==s["height_km"]]
        # A production configuration can exceed one A100's memory capacity for
        # a large domain.  Retain that explicit failure in scaling_failures and
        # normalize the remaining curve to its smallest valid point instead of
        # aborting report generation or silently inventing a p1 baseline.
        baseline=min(candidates,key=lambda x:x["compute_ranks"])
        base=baseline["physics_sdpd_median"]
        s["baseline_compute_ranks"]=baseline["compute_ranks"]
        s["one_resource_baseline_available"]=(baseline["compute_ranks"]==1)
        s["speedup"] = s["physics_sdpd_median"] / base
        if s["kind"] == "weak":
            # Work per compute resource is held fixed, so efficiency is the
            # baseline duration divided by this point's duration.
            s["parallel_efficiency"] = s["speedup"]
        else:
            # If p1 is unavailable (for example due device capacity), retain
            # the clearly labeled smallest valid point as the relative base.
            s["parallel_efficiency"] = s["speedup"] / (s["compute_ranks"] / baseline["compute_ranks"])
    (out/"scaling_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    lines=["# HICAR scaling report", "", "| platform | kind | domain km | compute ranks | repeats | timed SDPD median | range | baseline |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for s in summary: lines.append(f"| {s['platform']} | {s['kind']} | {s['width_km']}×{s['height_km']} | {s['compute_ranks']} | {s['repeats']} | {s['physics_sdpd_median']:.3f} | {s['physics_sdpd_min']:.3f}–{s['physics_sdpd_max']:.3f} | p{s['baseline_compute_ranks']} |")
    if failures:
        lines += ["", "## Unsupported or incomplete points", "", "| platform | scenario | repeat | reason |", "|---|---|---|---|"]
        for f in failures: lines.append(f"| {f['platform']} | {f['id']} | {f['repeat']} | {f['reason']} |")
    (out/"scaling_report.md").write_text("\n".join(lines)+"\n")
    try:
        import matplotlib.pyplot as plt
        plot_scaling_guides(summary, out)
        plot_timer_components(rows, out)
        for platform in sorted({s["platform"] for s in summary}):
            plt.figure(figsize=(7,4))
            for kind in ("strong", "weak"):
                curves=defaultdict(list)
                for s in summary:
                    if s["platform"]==platform and s["kind"]==kind: curves[(s["width_km"],s["height_km"])].append(s)
                for domain,curve in curves.items():
                    curve.sort(key=lambda x:x["compute_ranks"])
                    plt.plot([x["compute_ranks"] for x in curve],[x["parallel_efficiency"] for x in curve],marker="o",label=f"{kind} {domain[0]}×{domain[1]} km")
            plt.xscale("log",base=2); plt.ylim(0,1.05); plt.xlabel("compute ranks / GPUs"); plt.ylabel("parallel efficiency"); plt.legend(); plt.tight_layout(); plt.savefig(out/f"{platform}_parallel_efficiency.png",dpi=160); plt.close()
    except ImportError:
        pass

if __name__ == "__main__": main()
