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

A wind-climatology output candidate now integrates exact adaptive timesteps
into hourly vector means, scalar means, and maxima of six ten-minute scalar
means at 50, 75, 100, 125, 150, 200, and 250 m AGL, plus the corresponding
10-m fields for SwissMetNet verification. The production profile adds only six
hourly surface health/context fields; static geometry is not repeated. It is
38 float32 plane-equivalents per hour, about 78.6 TB raw for 20 years on the
2.95-million-cell domain versus about 583 TB for the former 47-plane,
ten-minute evaluation profile. The ten-minute maximum is a sustained-wind
diagnostic, not a model gust. Cold starts are encoded with a real NetCDF fill
value and campaign segments are restricted to whole-hour boundaries so hourly
accumulators are empty at checkpoints.

The final one-node GPU/NCCL gate (build job `5117193`, integration job
`5117508`) completed a continuous two-hour Gaudergrat run and an independent
restart from hour one. All eight terminal wind fields were bitwise equal,
cold-start masks and terminal finite coverage were complete, and scalar/vector
plus ten-minute-maximum invariants passed with a 5e-4 m/s float32 tolerance.
Verified shuffle+deflate-1 compaction reduced the 10.93 MB smoke file to
3.80 MB (2.88:1), but its one-third fill-record content makes the ratio
unsuitable for capacity planning. Exact evidence is in
`case_studies/swiss_200m/validation/wind_climatology_output_v1.json`; a
representative production-segment compression and throughput measurement
remains required before enabling automatic compaction or reserving storage.

HICAR production is pinned at `0b9b0cb6`, the qualified GPU RRTMGP restart-
reproducibility merge. The later `0545a55d` fused theta-reduction optimization
and its `5503dacd` hourly-wind-output descendant are research-only: fresh exact
national reruns exposed nondeterministic invalid state as described below.
The 8-day national campaign also exposed a remaining asynchronous execution
hazard in `0b9b0cb6`: winter attempts `5118606` and `5118666` first developed
non-finite surface/thermodynamic state at 00:40 and 01:20 respectively, despite
identical inputs and bounded winds. With `NVCOMPILER_ACC_SYNCHRONOUS=1` and
deferred uploads disabled, the otherwise identical two-hour job `5118697` and
12-hour job `5118701` completed in 7:54 and 29:42. The production-length gate
validated 73 exact ten-minute records, 13 forcing frames, a 12:00 restart, and
10/50 m maxima of 28.53/26.72 m/s. The selected long-run execution is therefore
synchronous; asynchronous GPU execution is not production-qualified for this
national winter state. This is an execution-reliability workaround, not a
scientific configuration change, and its throughput cost must remain visible.
Static/forcing validation preserves exact
HHL/HFL geometry, chronology, finite/range contracts, and runtime-domain
identity. Deterministic eight-worker preparation reduced a representative
national record from about 80 to 16 minutes. Exact 32,768-target RBF chunks
reduced a worst-case temporary from 18.88 GB to 210 MB; this is a latency/
allocation fix, not a general throughput multiplier.

A fresh 2061 x 1431 x 80 input profile at coordinator `551d8ed` separated the
recurring record path from decoding. With eight column workers, target
transformation plus regular and sparse writes took 797 s and peaked at
241,376,432 KiB aggregate RSS. Column reconstruction was 361 s, horizontal
remapping 266 s, regular NetCDF writing 107 s, and sparse-LBC writing only
19 s. Sixteen workers produced byte-identical regular and sparse files and cut
generation to 654 s (-18%), but aggregate RSS rose to 424,659,768 KiB (+76%)
and process CPU rose 5%; retain eight workers as the efficient default. For
regular-relaxation campaigns, sparse LBC now defaults off.

The follow-up real-domain optimization retained eight forked column workers,
removed repeated profile sorting/validation and log-pressure work, and added a
serial Numba RBF kernel that accumulates fixed donor stencils without donor or
weighted-product gathers. Horizontal remapping fell from 266.0 to 52.1 s
(-80.4%), column reconstruction from 361.1 to 290.3 s (-19.6%), transform from
647.5 to 432.8 s (-33.2%), and full profiled generation from 797.1 to 589.4 s
(-26.1%) at essentially unchanged Slurm peak RSS. A compiled whole-column
prototype was rejected: at national scale its per-column allocation and memory
traffic regressed to 415.1 s at eight threads and did not scale efficiently.
Process-local file-identity checksum caching removes about 10.5 s of duplicate
hashing; omitting unselected sparse LBC saves another 19.2 s and 796 MB.

The accelerated forcing passed production validation. Only 68 serialized
float32 values differed from the NumPy baseline; maxima were 0.00390625 Pa,
9.54e-7 m/s, and 4.66e-10 moisture. A 12-node/48-GPU two-hour HICAR pilot
completed all 13 outputs and the exact terminal restart with bounded winds.
The final trajectory is not bitwise equal to the baseline. Across all 13
outputs, normalized RMS differences were `3.6e-5` for 2-m temperature,
`5.2e-5`/`9.2e-5` for 10-m wind, and `5.5e-4` for 2-m humidity; threshold-
sensitive PBL height and sensible-heat flux reached 1.5% and 1.2%. The backend
is therefore a qualified scientific-equivalence path rather than a
reproducibility path; retain `numpy` when exact historical trajectory identity
is required. Evidence is in `hicarprep_swiss_input_performance_v1.json` and
`hicarprep_swiss_input_optimization_v1.json` in the Swiss validation directory.

The recurring NetCDF path was then qualified separately. Lossless deflate
level 1 cut regular-record writing from 107.9 to 66.3 s (-38.5%) while keeping
every decoded variable bitwise identical; the file grew from 4.49 to 5.04 GB
(+12.1%). Sparse frames now store the precision actually consumed by HICAR
(float32), use level-1 deflate, gather in 4,096-point chunks, compute edge
support without duplicate domain meshgrids, and overlap the required regular-
record checksum with compression. Sparse writing fell from 19.23 to 3.95 s
(-79.5%) and size from 796 to 330 MB (-58.5%). Every sparse value is exactly
the old float64 product cast to float32, so values delivered to HICAR are
unchanged. The measured write-phase saving is 56.9 s per hourly record, or a
conservative 9.6% additional end-to-end improvement at fixed transformation
cost; the observed full profile was 457.2 s (-22.4%), with node variation in
the transform. Evidence is in `hicarprep_swiss_boundary_optimization_v1.json`.
Separating cycle-invariant geometry from standalone hourly regular records now
requires a reader/schema change and is not a low-risk storage optimization.

The next Swiss-scale input pass qualifies the remaining recurring kernels and
publication path.  A short-lived column geometry plan with one bulk validation,
an exact zero-QI path tied to decoder provenance, and copy-free water conversion
were exact relative to the accepted transform.  In a full-domain prototype that
also fused terrain-W-to-HFL interpolation, the median profiled hourly record
fell from 457.2 to 393.4 s (-14.0%): columns fell 19.7%, W processing 64.8%,
water conversion 40.5%, and Slurm peak RSS about 10%.  Two independent runs and
a serial-RBF control produced the same prototype hashes.  The fused kernel's
only numerical change was 43 W values by at most one float32 ULP (1.49e-8 m/s;
normalized RMS 3.29e-12), but the required two-hour GPU HICAR pilot amplified
it into non-finite surface diagnostics.  Fused W is therefore rejected for
production; the exact reference W path remains selected.  Joined fixed-order
RBF threads remain useful for smaller arrays (up to 3.2x in isolated tests)
but were neutral at national scale, so national production defaults to one
RBF thread.  Retaining one Python remap object per 2.95 million columns and
persisting a second SST RBF operator were rejected as memory/I/O regressions.

The native adapter now defaults to lossless deflate level 1: decoded arrays
are bitwise identical to levels 0 and 4, decoding took 40 s instead of 63 s at
level 4, and the 1.40 GB file remains 79% smaller than the 6.67 GB uncompressed
record.  Publication reuses the campaign's trusted static digest and validates
a checksum-bound receipt plus metadata after atomic rename; the full science-
array validator remains the qualification/default diagnostic.  The receipt
check took 12.45 s versus 53.5--53.9 s for the paired full checks.  The selected
reference-W sequence took a median 471 s from decode through ready publication,
at least 25% below the previous 628 s known stages even though the earlier total
excluded SST preparation.  The rejected fused-W prototype took 431 s.
The selected path passed the 12-node/48-GPU two-hour HICAR gate with all 13
outputs, a valid terminal restart, finite diagnostics, and 10/50 m maximum
winds of 16.76/17.56 m/s.  The trajectory is not bitwise identical to the
accepted pilot but stays inside the previously qualified equivalence envelope:
normalized RMS is 3.50e-5 for 2-m temperature, 5.42e-5/8.28e-5 for 10-m wind,
5.36e-4 for 2-m humidity, 1.42% for threshold-sensitive PBL height, and 1.21%
for sensible-heat flux.  Evidence is in
`hicarprep_swiss_bc_acceleration_v1.json`.

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

Initial real-scale qualification used the 2061 x 1431 x 80 terrain-radiation
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
variable restart state were bitwise identical to the continuous run. However,
fresh exact reruns on 2026-08-17 with the same executable, static, forcing list,
rendered namelist and 12-node topology failed validation after successful model
integration: most interior thermodynamic and surface diagnostics were non-finite.
The same failure occurred for both Sx-off and Sx-on configurations, while an
otherwise identical fresh run with the parent production executable at
`0b9b0cb6` completed all 13 records, passed finite-core validation, produced a
valid terminal restart, and bounded 10/50 m winds at 16.76/17.56 m/s (job
`5118144`). The five-line fused nested OpenACC reduction is therefore not
production-qualified despite its earlier repeated passes; its behavior is
consistent with a scheduling-dependent GPU reduction defect. Retain the
performance measurements only as rejected optimization evidence and use
`0b9b0cb6` for campaigns. Exact earlier-pass evidence is in
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
The transient wind-output candidate build and validated restart case are under
`$SCRATCH/icon_hicar/wind_climatology_20260816`; the versioned evidence manifest
contains their exact commit, executable checksum, job IDs, and report paths.
