# ICON-HICAR experimental evidence

## Four-event campaign result

The locked 147-station, four-season comparison produced:

| Result | DJF | MAM | JJA | SON |
| --- | ---: | ---: | ---: | ---: |
| Sx-off speed RMSE (m s-1) | 1.914 | 1.469 | 1.550 | 3.257 |
| Sx-off vector RMSE (m s-1) | 2.556 | 2.257 | 2.289 | 5.059 |
| Sx-off minus Sx-on speed | -0.214 | -0.330 | -0.286 | +0.093 |
| Sx-off minus Sx-on vector | -0.069 | -0.102 | -0.052 | +0.454 |

Sx-on was mixed: autumn vector RMSE improved, but speed RMSE degraded in the
other three events. Sx-off was materially neutral against REA-L in all eight
event/metric tests: it repaired common/weak-regime over-damping but over-
amplified the strong autumn regime. The matched interpolation-only control
reproduced native-REA-L station RMSE to `1.8e-15 m s-1`; HICAR was neutral to
it in DJF/MAM/JJA and better only in SON. The effect is event-dependent.

All 16 campaign segments passed. The 69-file evidence is published at
`/store_new/mch/msopr/olifu/icon_downscaling/swiss_200m/`
`evaluation_regular_relaxation_rrtmg_water_alpha1_sxoff_v1`; its `SHA256SUMS`
digest is `17220dad092120caef5ed3c6a15de02037433a880a250a912acc5edec6e8f247`.

## Reference and engineering state

The scientific specification and literature boundary are in
`case_studies/swiss_200m/REFERENCE_SETUP.md`. In brief: 2061 x 1431 cells at
200 m; 80 SLEVE levels to 15 km; native hourly REA-L through `hicarprep`;
regular full-domain relaxation; fixed alpha 1 and Sx/TPI off; Morrison, YSU,
Noah-MP/revised-MM5, prescribed water temperature, and 600 s radiation.
Evaluation covered horizontal surface wind; terrain-following W, cloud,
precipitation, lake thermodynamics, and slow land state remain limitations.

HICAR production is `feature/icon_downscaling` at `0545a55d`; it advances the
qualified RRTMGP state with the theta-reduction scheduling change described
below. Static/forcing validation preserves exact
HHL/HFL geometry, chronology, finite/range contracts, and runtime-domain
identity. Deterministic eight-worker preparation reduced a representative
national record from about 80 to 16 minutes. Exact 32,768-target RBF chunks
reduced a worst-case temporary from 18.88 GB to 210 MB; this is a latency/
allocation fix, not a general throughput multiplier.

The completed campaign used host RRTMG inside an otherwise GPU model. Median
radiation cost was 4301/5452 model seconds (75.7%): the wrapper transferred the
whole domain and ran serial `ncol=1` SW/LW columns on one CPU core per compute
rank. GPU RTE-RRTMGP v1.9.3 is qualified for new experiments when both
`KERNEL_MODE=accel` and `RTE_KERNEL_MODE=accel` are set. HICAR resets Noah-MP's
hidden energy workspace and uses RRTMGP's sequential tropopause `minmaxloc`
helper for NVHPC OpenACC, removing the former topology-sensitive differences.

The one-node restart gate, two independent 12-node/48-GPU Swiss replicas, and
the full continuous/restarted case were bitwise identical at all 13 outputs
and across all 199 compared join/endpoint variables. Executable SHA-256 is
`1f2eb75129c03cda74e0df9435fd9145b3c1a390ad988b7a6a961eebacebdd5e`.
Full-domain two-hour radiation was 4.875--4.881 s and total model time
210.080--211.828 s. Replacing the old radiation share estimates 4.06x model
and 3.56x wall speedup; this is not a measured 12-hour result and does not
alter the completed RRTMG trajectory.

After that radiation acceleration, a fresh whole-model GPU profile at the same
production commit used the bundled 164 x 142 x 40 Gaudergrat case on one
Balfrin node with four compute ranks and one I/O rank. A three-hour release
baseline spent 23.231 s in physics, led by 12.001 s advection and 4.082 s LSM;
radiation was only 0.552 s. Nsight rank-0 tracing recorded 454,004 kernel
launches and 357,159 stream synchronizations per simulated hour; launch and
synchronization API time dominated and no kernel exceeded 8.2% of GPU kernel
time. A factorized, repeated three-hour A/B showed that NVHPC deferred uploads
with a one-byte threshold—not CUDA Graph capture—provided the improvement.
Across two independent pairs, mean physics time fell from 23.265 to 22.462 s
(-3.5%), total model time from 40.045 to 39.112 s (-2.3%), and flux correction
from 2.539 to 1.854 s (-27.0%). Graph capture alone improved total time by only
0.3% in its pair and made the deferred-upload case 0.5% slower. CDO found no
data differences in either pair's output or terminal restart. Only deferred
uploads are retained as an opt-in, default-off setting pending a representative
crop A/B; this small case is not a national-domain speedup claim.

The next profile-directed intervention fused the theta extrema calculation's
host-driven vertical loop into one OpenACC kernel with an outer `gang` loop and
inner `vector` reductions. This is a five-line scheduling-only source change;
the min/max algorithm and subsequent MPI reductions are unchanged. Across two
fresh three-hour control/candidate pairs, mean total time fell from 39.755 to
36.871 s (-7.3%), physics from 23.179 to 19.932 s (-14.0%), and advection from
11.989 to 8.717 s (-27.3%). Adding the already qualified deferred-upload
setting reduced those means to 36.225, 19.255, and 8.072 s, respectively: a
cumulative -8.9%, -16.9%, and -32.7% versus control. All four candidate output
and terminal-restart comparisons had no CDO data differences. A follow-up
one-hour Nsight trace reduced rank-0 kernel launches from 454,004 to 394,912,
stream synchronizations from 357,159 to 270,391, and asynchronous device-to-host
copies from 66,657 to 8,313; the profiled theta construct fell from 29,916 calls
to 748. The full four-rank GPU unit executable is not an acceptance gate here:
both the unchanged and candidate builds hit the same existing OpenACC
present-table failure in the advection sequence, while the production-topology
whole-model controls and candidates completed.

Real-scale qualification then used the 2061 x 1431 x 80 terrain-radiation
Swiss domain, NVHPC 24.5 release executable
`a07aa804aba9bd764e0a82c9efa9b332b5167337cd0fbd429cb27834b30a6026`,
RRTMGP, deferred uploads, 12 nodes, 48 compute GPUs, 12 I/O ranks, and a
two-hour 2020-10-01 case. Cold A/A jobs `5114306` and `5114309` completed in
6:01 and 6:05. All 30 stored output variables were encoded-bit identical at
all 13 ten-minute times, and all 199 terminal-restart variables, including
coordinates, were bitwise identical over the physical model core. Whole-file
SHA-256 values differ only because the creation timestamp and replica-specific
output/restart paths are global attributes; all other metadata and layout
properties are identical. Strict segmented jobs `5114334` and `5114379`
completed in 4:31 and 4:56. Their complete 13-time trajectory and final 199-
variable restart state were bitwise identical to the continuous run. This
qualifies the scheduling change and deferred-upload runtime setting for
production correctness; the measured speedups remain the controlled small-case
results above, not a national-domain A/B claim. Exact run evidence is in
`case_studies/swiss_200m/validation/gpu_theta_reduction_12node_qualification_v1.json`.

A production-node scaling study on the same two-hour Swiss case retained one
RRTMGP batch per compute rank. Two instrumented 12-node replicas completed in
6:01 and 5:57 with mean model time 260.85 s; peak sampled A100 memory was
90,640/98,304 MiB, leaving 7,664 MiB. All 13 output states and the terminal
restart were bitwise exact between replicas, and the continuous/restarted
endpoint was exact across 30 output and 198 physical restart variables. Eleven
nodes was slower (306.54 s model time), left only 990 MiB on one sampled GPU,
and failed scientific validation with non-finite core fields. Eight nodes
failed an RRTMGP 6.70 GB allocation with CUDA out-of-memory; the still-larger
six-node one-batch tile was therefore not submitted. Retain 12 nodes and one
active `normal` model as the safe production setting. The 46-node installed
count does not imply 46 campaign-available nodes: 21 nodes were in named
reservations during the concurrency trial, and a second 12-node segment could
not co-schedule. Extra segments should use `lowprio`/`preemptible`
opportunistically or a coordinated share exception. Exact evidence is in
`case_studies/swiss_200m/validation/hicar_gpu_node_scaling_20260816_v1.json`.

## Retained state

Balfrin `$SCRATCH/icon_hicar/gpu_perf_20260816` retains the production and
theta-reduction GPU/NCCL builds, immutable benchmark runs, Nsight traces, and
real-scale qualification inputs/reports. It is transient working state, not a
published artifact. Durable storage retains only the final
campaign evidence and `swiss_200m/hicar_surface_verification_v1`, whose digest is
`a26f6a524deb7f41f67302d4bf56103102a453a0d2ef51f276cfa2b0524ff644`.
Legacy recovery/qualification data are not recoverable. Git retains coordinator
`main` and HICAR `main` plus `feature/icon_downscaling`.
