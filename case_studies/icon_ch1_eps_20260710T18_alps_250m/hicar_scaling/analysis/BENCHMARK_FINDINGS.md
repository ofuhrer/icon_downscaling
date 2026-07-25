# HICAR 250 m CPU/GPU benchmark findings

This is the durable handoff for the six-hour real-ICON, 80-level production
configuration on Balfrin. It describes timed integration performance; it does
not include queue time, staging, initialization, or output costs.

## What was measured

- CPU: AMD EPYC 7713. One model socket-equivalent is 32 compute MPI ranks;
  the separate I/O rank is excluded.
- GPU: NVIDIA A100 96GB. One model socket is one compute rank/GPU; the I/O
  rank is CPU-only.
- Each valid point has three independent repetitions, except the historical
  two-repeat GPU p24 points. Figures normalize by the inferred number of
  integration steps, because the adaptive timestep differs among domains.

## Operational takeaways

- GPU is substantially faster for the measured production setup. At 240x160
  km / 250 m, strong scaling reaches 1,977 timed SDPD on 24 A100s, compared
  with 175 timed SDPD on eight CPU socket-equivalents (256 compute ranks).
- The 240x160-km CPU strong curve is useful from p64 through p256. Smaller
  points were excluded because they project beyond the four-hour benchmark
  limit. The p256 median is 174.63 timed SDPD.
- GPU small-domain strong scaling saturates as the grid work per A100 falls:
  80x80 km improves from 1,985 timed SDPD on one GPU to 5,428 on 24 GPUs, but
  timestep-normalized efficiency is only about 11% at 24 GPUs.
- GPU large-domain scaling remains useful: 240x160 km goes from 1,628 timed
  SDPD on 12 GPUs to 1,977 on 24 GPUs (about 61% efficiency relative to p12).
- CPU weak scaling degrades strongly as the number of sockets grows: the
  critical-path timestep cost rises from 104 ms at p1 to 560 ms at p256.
  GPU weak scaling rises from 32 ms on one A100 to 83 ms on 24 A100s.
- CPU 80x80-km scaling is limited primarily by max-rank advection time at low
  rank counts, not by halo exchange. GPU saturation is consistent with small
  per-GPU working sets plus launch/synchronization overhead.

## Capacity and scheduler limits

- A 240x160-km production run cannot fit on one or two A100s: p2 failed in
  RRTMGP with `CUDA_ERROR_OUT_OF_MEMORY`. Treat p12 as the first validated
  point for that GPU strong curve; do not infer a p1 baseline.
- The p384 CPU tasks remain pending as `QOSGrpCpuLimit`. pp-long has physical
  capacity, but its 1,280-CPU QoS group is already allocated and each task
  needs 390 CPUs. They have no start guarantee and are not measurements.
- Historical Slurm accounting has no usable CPU RSS or GPU-memory high-water
  values. Future benchmark launchers measure CPU rank RSS with `/usr/bin/time
  -v` and sample GPU device memory once per second; fit a memory model only
  after those runs exist.

## Performance model

`build_performance_model.py` estimates timed seconds per timestep from domain
grid cells per socket and socket count:

```
log(t_step) = c0 + c_work log(cells/socket) + c_socket log(sockets)
              + c_interaction log(cells/socket) log(sockets)
```

The interaction is intentional: it represents the observed fact that the
parallel penalty changes as working sets become small. CPU sockets are 32
compute ranks; GPU sockets are A100 devices. The fit has 13.3% CPU and 3.8%
GPU median in-sample relative error. Use `--timestep-s` whenever a production
timestep estimate is available. Without it, the estimator uses the closest
measured domain and assumes timestep scales linearly with resolution.

The model is interpolation only: it is calibrated to this forcing window,
250-m configuration, and 80-level grid. It flags socket or cells-per-socket
extrapolation and intentionally makes no memory prediction.

## Canonical artifacts

- `cpu_gpu_timestep_normalized_scaling.png`
- `cpu_gpu_timestep_normalized_components.png`
- `performance_model.json` and `performance_model_report.md`
- `build_performance_model.py`
