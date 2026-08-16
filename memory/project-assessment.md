# ICON-to-HICAR scientific assessment

## Decision

Do **not** scale the present HICAR configuration to the 20-year REA-L-CH1
archive. Retain native REA-L/interpolation-only as the operational baseline.
Alpha-one with bundled Sx/TPI damping disabled is the bounded HICAR research
reference if a new event-regime question justifies more work; it is not
production-qualified and does not justify another tuning matrix.

Two controlled four-season national campaigns establish this:

- Sx/TPI on was mixed: vector RMSE did not degrade in any event and materially
  improved in autumn, but speed RMSE materially degraded in winter, spring,
  and summer.
- Sx/TPI off was neutral against REA-L for all eight event/metric comparisons.
  It repaired the first three speed penalties but lost the autumn vector gain.
- The matched interpolation-only control reproduced native-REA-L station RMSE
  to `1.8e-15 m s-1`. Sx/TPI-off HICAR was neutral to it in DJF/MAM/JJA and
  materially better in SON, showing an event-dependent dynamical effect but
  not robust four-event added value.

On the locked 147-station cohort, Sx-off speed/vector RMSE is `1.914/2.556`
(DJF), `1.469/2.257` (MAM), `1.550/2.289` (JJA), and `3.257/5.059 m s-1`
(SON). Relative to Sx-on, the changes are `-0.214/-0.069`, `-0.330/-0.102`,
`-0.286/-0.052`, and `+0.093/+0.454 m s-1`. Speed-variability ratios show the
mechanism: Sx-on over-damps common/weak regimes; Sx-off over-amplifies the
strong autumn regime. Correlation changes by at most `0.013`.

The Sx-off campaign completed all 16 validated 12-hour segments. Its compact
evidence is checksum-published at
`/store_new/mch/msopr/olifu/icon_downscaling/swiss_200m/`
`evaluation_regular_relaxation_rrtmg_water_alpha1_sxoff_v1`; the ready marker
binds 69 files through `SHA256SUMS` digest
`17220dad092120caef5ed3c6a15de02037433a880a250a912acc5edec6e8f247`.
Canonical evaluation-manifest, national-summary, interpolation-control, and
HICAR/control-comparison digests begin `f7cd9289`, `6f0897cf`, `c015c3d5`, and
`8cdcfc2e`.

## Scientific reference

The detailed rationale is in `case_studies/swiss_200m/REFERENCE_SETUP.md`.
No published national all-season 100--200 m HICAR wind validation or
multi-year continuous atmospheric HICAR run was found through August 2026, so
this is an untuned coherent reference, not a demonstrated optimum.

- Domain: 200 m AEQD, 2061 x 1431, centred 46.815 N/8.225 E, with a 40 km
  external margin. GLO-30 surface terrain relaxes to REA-L `HSURF` over 30 km.
- Vertical grid: 80 SLEVE levels to 15 km, nominal 20 m lowest layer, stretch
  0.65, split smoothing 5 cells/10 cycles, decay 2/6, exponent 1.35. Minimum
  interface spacing/Jacobian is 17.008 m/0.3375; 12 m is the acceptance floor.
- Input: hourly native REA-L through maintained `hicarprep`; the fieldextra
  route is retired. Regular forcing supplies P/T/dry QV/QC/QI, earth-relative
  U/V, target-HFL W, and valid-time water temperature. The campaign uses
  regular full-domain relaxation, not experimental sparse LBC.
- Water: use compact same-surface remapping where supported and otherwise the
  exact-valid-time monotone local all-surface RBF baseline. The old nearest-
  water fallback reached 55.67 km and caused multi-kW m-2 fluxes; it is
  rejected. Frozen-lake physics remains absent.
- Dynamics: RK3, CFL 1.6, third-order advection, FCT 1, density transport,
  adjoint wind projection with fixed alpha 1, Sx/TPI off, two passes, 2500
  iteration cap, hourly updates. Dynamic alpha produced localized 139 m s-1
  initialization winds and is rejected for this fork/domain.
- Physics: Morrison, YSU, Noah-MP/revised-MM5, simple prescribed water,
  terrain-corrected RRTMG at 600 s, and no cumulus. External LAI/VEGFRA/albedo
  are not inputs under `dveg=3`, `alb=2`.
- Scope: horizontal surface-wind downscaling only. Terrain-following W reaches
  roughly -25 m s-1 in forcing, and early precipitation adjustment can be
  large; W, cloud, and precipitation skill remain unqualified.

The fixed evaluation used 24 ending-hour pairs after 24 h spin-up in each of
four 48 h trajectories (Jan 14--16, Apr 28--30, Jun 30--Jul 2, Oct 1--3 2020).
Vector and speed RMSE were co-primary. Materiality was strictly greater than
`max(0.10 m s-1, 5% of REA-L RMSE)`. The preregistered replicated-improvement
gates and ridge/valley/high-elevation safeguards failed closed; both final
campaigns missed added-value qualification without a repeated safeguard
failure.

## Engineering state

- HICAR production is `feature/icon_downscaling` at merge `0b9b0cb6`; its tree
  equals qualified GPU-RRTMGP commit `cd94b79b`. The coordinator submodule must
  point to that merge.
- Static construction is reproducible and preserves authoritative HHL/HFL.
  Runtime-domain identity covers invariant terrain/masks/soil plus explicitly
  joined epoch/time-varying fields.
- Forcing validation checks exact geometry, chronology, finite/range contracts,
  and complete runtime-domain identity before shared publication.
- Deterministic eight-worker national transformation reduced a representative
  record from about 80 to 16 minutes. Contiguous 32,768-target RBF chunks keep
  donor order/arithmetic exact and reduce a 18.88 GB temporary to at most
  210 MB; the qualified 9.56 GB record was bit-identical. This fixes worst-case
  remap latency/allocation, not a general throughput multiplier or evidence
  that two records fit safely on one node.
- Land initialization uses native REA-L TERRA soil temperature/water, deep-soil
  temperature, and snow amount/depth/density/bulk temperature. Frozen phase,
  glacier history, lake thermodynamics, and slow Noah-MP state remain
  approximations.
- Validators require expected output times, selected physics and forcing
  turnover, successful termination, and terminal restart content. Timestamp
  `+0.432 s` is known serialization metadata, not a physics timestep.

The completed campaign used CPU RRTMG inside an otherwise GPU model. Median
radiation cost over 16 segments was 4301 of 5452 model seconds (75.7%); RRTMG
copied the full domain to the host and ran serial `ncol=1` SW/LW columns on one
CPU core per GPU rank. Longwave/McICA dominated CPU samples. Noah-MP, Morrison,
YSU/PBL, advection, wind projection, and halo exchange ran on GPU.

GPU RTE-RRTMGP v1.9.3 is now qualified for new experiments:

- Set both `KERNEL_MODE=accel` and v1.9.3's `RTE_KERNEL_MODE=accel`; setting
  only the old option silently selects CPU kernels.
- HICAR resets Noah-MP's hidden energy workspace before each call and makes
  NVHPC OpenACC use RRTMGP's sequential tropopause `minmaxloc` helper. This
  removes topology-sensitive 32-column radiation differences caused by NVHPC
  24.5 device `MINLOC`/`MAXLOC` scalar-live-out code generation.
- A 495 x 495 x 80 one-node case matches the national cells/GPU within 0.3%.
  Its RRTMGP radiation time was within 5% of full Swiss, so it is a useful
  radiation benchmark but not a whole-model scaling proxy.
- The one-node restart gate, two independent full-Swiss 12-node/48-GPU cold
  replicas, and full-Swiss continuous/restarted run were bitwise identical at
  all 13 outputs and across all 199 compared variables at join and endpoint.
  Qualified executable SHA-256:
  `1f2eb75129c03cda74e0df9435fd9145b3c1a390ad988b7a6a961eebacebdd5e`.
- National two-hour radiation was 4.875--4.881 s and total model time
  210.080--211.828 s. Replacing the original 12-hour RRTMG radiation share
  gives an Amdahl estimate of 4.06x model and 3.56x wall speedup (about 22.1 vs
  78.5 minutes), holding cadence and non-radiation work fixed. This is not a
  measured 12-hour GPU-RRTMGP run.

This new path does not alter the completed RRTMG campaign trajectory. Its
historical continuous/restarted outputs were numerically, not bitwise,
reproducible, but the wind perturbation was negligible for that assessment.

## Closed and open questions

Closed: fixed Sx/TPI choices do not provide robust four-event added value;
interpolation-only remains operational. The GPU-RRTMGP restart-reproducibility
investigation is complete, merged, and its dedicated development data and
branches are disposable after the synthesis above is retained.

Open only if a new scientific question warrants work: regime-conditioned
damping, terrain-W conditioning, lake thermodynamics, and cold-cloud/
precipitation spin-up. Do not tune individual parameters or resume a generic
production campaign without a hypothesis that changes the decision.
