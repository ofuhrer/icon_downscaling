# HICAR benchmark performance model

Calibrated to: HICAR production configuration, 80 levels, 250 m benchmark forcing.

The model estimates timed integration wall time. CPU socket-equivalents are 32 compute MPI ranks; a GPU socket is one A100 96GB.

`log(t_step) = c0 + c_work×log(cells/socket) + c_socket×log(sockets) + c_interaction×log(cells/socket)×log(sockets)`

| Platform | median absolute error | calibration sockets | cells/socket |
|---|---:|---:|---:|
| cpu | 13.3% | 0.03125–8 | 25600–3276800 |
| gpu | 3.8% | 1–24 | 4267–102400 |

## Use

Run this script with `--estimate ...`. Supply the production timestep when known. Without it, the script selects the nearest measured domain and scales its timestep linearly with resolution; that is a CFL assumption, not a forecast.

## Guardrails

- Timed integration only: initialization, I/O, queueing, and staging are excluded.
- Calibrated only to the listed 250 m, 80-level production configuration and ICON forcing window.
- Resolution changes scale grid-cell count exactly but timestep only by an assumed CFL-linear dx relation unless supplied explicitly.
- Legacy measurements contain no reliable memory high-water marks; this model does not predict capacity or memory.
- Estimates outside the calibrated socket or working-set range are explicitly marked extrapolated.
- The new benchmark launcher records per-rank CPU RSS and GPU memory peak for future memory fits; historical runs have no usable Slurm memory values.
