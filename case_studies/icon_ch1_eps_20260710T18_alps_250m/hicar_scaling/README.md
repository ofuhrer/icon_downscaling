# HICAR CPU/GPU scaling suite

`setup_scaling_suite.py` generates CPU and NCCL-GPU strong scaling on 80×80 km and 240×160 km domains, plus resource-proportional weak scaling through six nodes. GPU nodes use four compute A100s plus a CPU-only I/O rank; the I/O rank has CUDA hidden and does not consume a GPU, so the suite reaches 24 compute GPUs across six nodes. All runs use the six-hour `v2_auto1_n80_top12_s26` wind configuration and hourly output.

On Balfrin, first run the CPU/GPU build-and-validation jobs, generate the suite with `SCALING_ROOT=$SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_scaling python3 setup_scaling_suite.py`, then stage real-data domains with `prepare_domains.sh`. Each run script refuses to start until both its domain `PREFLIGHT_OK` and platform binary `provenance/<platform>_READY` gates exist.

Run `analysis/analyze_scaling.py --root "$SCALING_ROOT"` after completed jobs. It produces raw JSON/CSV plus a repeat-median Markdown report; timer values retain HICAR min/mean/max values for detailed follow-up.

Every newly generated benchmark run records memory independently of Slurm accounting: CPU runs write per-rank peak resident set size (KiB), while GPU compute ranks sample peak device memory (MiB) once per second. `analyze_scaling.py` carries the max/mean and sample counts into `scaling_runs.json` and `scaling_runs.csv`; absent values identify legacy runs that predate this protocol.

`analysis/plot_cpu_gpu_comparison.py` produces the timestep-normalized cross-platform plots. `analysis/build_performance_model.py` fits the deliberately small timed-phase model and can estimate a configuration, for example:

```bash
python3 analysis/build_performance_model.py \
  --cpu-runs /path/to/cpu/analysis/scaling_runs.json \
  --gpu-runs /path/to/gpu/analysis/scaling_runs.json --out analysis \
  --estimate --platform gpu --width-km 240 --height-km 160 \
  --resolution-m 250 --sockets 24 --hours 6 --timestep-s 7.9
```

The model is an interpolation of this 250 m, 80-level production configuration. It predicts timed integration only and reports extrapolation; it deliberately does not predict memory until runs with the new high-water instrumentation are available.
