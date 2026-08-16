# ICON-to-HICAR scientific assessment

## Decision

Do **not** scale the tested HICAR configuration to the 20-year REA-L-CH1
archive. Native REA-L/interpolation-only remains the operational baseline.
Fixed-alpha-one with bundled Sx/TPI damping off is a bounded HICAR research
reference only when a new event-regime hypothesis justifies work; it is not
production-qualified and does not open another tuning matrix.

The locked 147-station, four-season evidence is decisive:

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
it in DJF/MAM/JJA and better only in SON. This is a real event-dependent
dynamical effect, not robust four-event added value.

All 16 campaign segments passed. The 69-file evidence is published at
`/store_new/mch/msopr/olifu/icon_downscaling/swiss_200m/`
`evaluation_regular_relaxation_rrtmg_water_alpha1_sxoff_v1`; its `SHA256SUMS`
digest is `17220dad092120caef5ed3c6a15de02037433a880a250a912acc5edec6e8f247`.

## Reference and engineering state

The scientific specification and literature boundary are in
`case_studies/swiss_200m/REFERENCE_SETUP.md`. In brief: 2061 x 1431 cells at
200 m; 80 SLEVE levels to 15 km; native hourly REA-L through `hicarprep`;
regular full-domain relaxation; fixed alpha 1 and Sx/TPI off; Morrison, YSU,
Noah-MP/revised-MM5, prescribed water temperature, and 600 s radiation. Claims
are limited to horizontal surface-wind downscaling; terrain-following W, cloud,
precipitation, lake thermodynamics, and slow land state remain limitations.

HICAR production is `feature/icon_downscaling` at `0b9b0cb6`; its tree equals
qualified RRTMGP commit `cd94b79b`. Static/forcing validation preserves exact
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

## Retained state and admissible follow-up

Balfrin `$SCRATCH/icon_hicar` is intentionally empty; new work must recreate
its runtime and inputs. Durable storage retains only the final campaign
evidence and `swiss_200m/hicar_surface_verification_v1`, whose digest is
`a26f6a524deb7f41f67302d4bf56103102a453a0d2ef51f276cfa2b0524ff644`.
Legacy recovery/qualification data are not recoverable. Git retains coordinator
`main` and HICAR `main` plus `feature/icon_downscaling`.

Only a new hypothesis may reopen work: regime-conditioned damping, terrain-W
conditioning, lake thermodynamics, or cold-cloud/precipitation spin-up. Do not
resume generic production or parameter tuning without evidence that can change
the decision.
