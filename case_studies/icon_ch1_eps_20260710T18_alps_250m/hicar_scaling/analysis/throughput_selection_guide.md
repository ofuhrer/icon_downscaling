# Throughput selection guide

Timed-phase SDPD is based on the maximum compute-rank physics timer; ranges are repeat minima/maxima.

## GPU strong scaling

| domain | GPUs | median SDPD | repeat range | efficiency | interpretation |
|---|---:|---:|---:|---:|---|
| 80x80 km | 1 | 82.7 | 82.7-82.8 | 100% | relative to p1 |
| 80x80 km | 2 | 138.5 | 137.7-138.6 | 84% | relative to p1 |
| 80x80 km | 12 | 192.3 | 191.4-193.8 | 19% | relative to p1 |
| 80x80 km | 16 | 214.9 | 213.9-216.1 | 16% | relative to p1 |
| 80x80 km | 20 | 223.8 | 187.5-224.6 | 14% | relative to p1 |
| 80x80 km | 24 | 226.2 | 223.9-228.4 | 11% | relative to p1 |
| 240x160 km | 12 | 67.8 | 66.7-68.8 | 100% | relative to p12 |
| 240x160 km | 16 | 72.8 | 72.7-73.3 | 80% | relative to p12 |
| 240x160 km | 20 | 75.1 | 75.0-75.3 | 66% | relative to p12 |
| 240x160 km | 24 | 82.4 | 81.9-83.0 | 61% | relative to p12 |

## Important limits

- 240x160 km requires at least 12 A100 GPUs in this configuration; p1/p2 exhaust device memory.
- CPU: only the 10x10 km p1 weak point (204.6 timed-phase SDPD) is currently comparable. Re-run CPU scaling after the MPI/I/O fix; do not extrapolate it.
- The p20 80x80 km strong point has one low repeat; use the median and shown range rather than a single run.
