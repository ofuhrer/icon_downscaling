# REA-L-CH1 streaming production design

## Production unit

The production unit is a seven-day HICAR segment with 169 hourly forcing
records, hourly two-dimensional output, and a restart exactly at the segment
end. The completed national 72-hour summer qualification updates the
provisional seven-day 200 m rate to about 10.64 hours on four nodes. The
capacity-scaled 100 m base is about 21.29 hours, with a 15.96--31.93-hour
planning range; keep seven days there only if the live capacity benchmark
leaves operational margin, otherwise shorten the segment. Consecutive
segments share a restart directory and run sequentially. Independent chains
can run concurrently within partition capacity.

The seven-day unit above remains the qualified stable-partition/month-DAG
contract. Opportunistic `preemptible` execution uses the separate controller
in `../../../orchestration/`: it replans the same restart chain into at most
24-hour simulation slices, gives every retry a new immutable attempt
directory, and caps wall-time at six hours. A cancelled attempt is repeated
from the last validator-published predecessor boundary; its partial output is
never reused. Only heavy HICAR jobs enter `preemptible`. Forcing,
finalization, solver audits, and compression share one global `pp-short`
array capped at two active tasks.

The pre-emptible runner records scheduler signals and exits non-successfully
unless `model_chunk_completion.json.ready` already exists. It does not claim
that a 60-second cancellation grace can serialize a new restart. Multiple
restart chains require frozen independent-chain authorization, and production
mode additionally requires the published annual
`GO_20_YEAR_200M_PRODUCTION` decision. The current restart-physics candidates
are not made production-ready merely by this orchestration support.

At each valid UTC hour, forcing comes from that date's 00 UTC REA-L-CH1 cycle
and `step=hour`. Midnight uses the new cycle's step 0, never the previous
cycle's step 24. The two representations differ slightly but the new-cycle
rule is deterministic and avoids duplicate valid times.

## State machine

1. `create_chunk_plan.py` atomically publishes an immutable plan and forcing
   list.
2. A bounded `pp-short` array runs
   `produce_rea_l_stream_record_balfrin.sbatch`. Each task reads one narrow
   FDB record, reuses a once-per-cycle static cache, regrids it, validates it,
   publishes the HICAR forcing plus hashes, and removes native GRIB/work files.
3. `finalize_rea_l_stream_chunk_balfrin.sbatch` verifies every record and
   publishes `forcing_publication.json.ready`.
4. `run_rea_l_stream_chunk_balfrin.sbatch` consumes the published forcing,
   writes the routine output and an exact-end restart, and publishes
   `model_chunk_completion.json.ready`. Its default is four 200 m nodes
   (sixteen GPUs); set `HICAR_SWISS_CASE` and override the submission to
   sixteen nodes for the 100 m capacity candidate.
   Bounded qualification runs may additionally set
   `STREAM_RESTART_INTERVAL_RECORDS` to write sparse intermediate recovery
   checkpoints. Once a checkpoint is closed,
   `validate_restart_checkpoint_balfrin.sbatch` inventories its exact time,
   source revision, qualified 80-level dimensions, essential atmosphere/land
   state, positive model time step, size, and whole-file checksum, then
   publishes a ready-marked report. This is a header/schema/provenance gate;
   it does not claim to have scanned every value in the full restart state.
5. A bounded `pp-short` array runs
   `compress_hicar_stream_output_balfrin.sbatch` over HICAR's daily NetCDF
   files. `compress_output_file.py` uses shuffle/deflate level 1, compares
   lossless logical hashes for every variable, and publishes each compressed
   file atomically. Raw outputs are retained until these publications have
   been collected into the archive manifest.
6. `retire_forcing_chunk.py` verifies both publications, the model-provenance
   hashes when present, and every forcing hash. It is dry-run by default. With
   `--execute`, it withdraws every ready marker before removing only the
   forcing NetCDF payloads and transient daily cache. Plans, validations,
   manifests, output, and restarts remain. The month DAG publishes a durable
   `forcing_retirement.json.ready` report for every main and overlap chunk;
   the final assessor requires each report to prove executed retirement with
   the planned record count.
7. After the following segment passes,
   `retire_restart_boundary.py` verifies both completion manifests and both
   restart hashes before retiring the superseded restart. The month DAG
   retains the July 8 overlap-start checkpoint until trajectory equivalence
   passes, then publishes four `restart_retirement.json.ready` lifecycle
   reports for the superseded main-chain boundaries. It keeps the final main
   and uninterrupted-overlap checkpoints. Keep the current and previous
   boundary during execution, plus explicitly selected periodic checkpoints.
   Retaining every seven-day restart would consume about 40 TiB at 200 m and
   roughly four times that at 100 m.

No deletion is triggered by a partial model log or elapsed time. A forcing
chunk remains restartable after any producer or model failure.

## Parallel production

The CPU producer is faster than the measured 200 m consumer: the live
qualification took 61--85 seconds per forcing hour, while HICAR needs about
228 seconds per simulation hour in the completed 72-hour summer event. Use
two to four producer tasks
per active model chain, capped to keep transient FDB traffic and scratch use
bounded. The 100 m conversion is expected to take roughly four times longer,
so it needs a live benchmark and likely two to four workers per chain.

The scientific candidate layout is one initialized chain per year, but all
twenty chains cannot run simultaneously on Balfrin: they would require 80
nodes at 200 m or 320 nodes at 100 m, while `normal` currently has 46. At
continuous near-exclusive capacity, the summer-calibrated optimistic wave
lower bounds are 37.0--69.4 days for 200 m (eleven chains, two waves) and
347--694 days for
100 m (two chains, ten waves). Queueing, retries, maintenance, competing
production, and archive transfer increase real elapsed time. The implemented
REA-L-derived initializer now supplies skin and soil temperatures,
depth-conservative soil water, SWE, and snow height. This removes the generic
cold-start input, but it does not yet demonstrate equivalence to a continuous
chain: a multi-day overlap and an annual-cycle validation remain mandatory
before independent yearly chains are scientifically acceptable.

## Output contract

The routine profile is deliberately limited to eleven hourly two-dimensional
fields: precipitation, surface pressure, 2 m temperature and humidity, 10 m
wind components, downwelling shortwave, downwelling longwave, upwelling
longwave, ground heat flux, and emissivity. Sea-level pressure is excluded because it
is not populated by the current REA-L forcing contract. This profile must be
validated against the intended scientific use before production.

The `qualification` profile adds snowfall and graupel, sensible and latent
heat fluxes, skin temperature, albedo, snow and canopy stores, column and
layer soil state, both last-soil-step runoff components, restart-persistent
cumulative surface/subsurface runoff and signed net evaporation, and the
aquifer, derived groundwater, and wetland stores. The last-step runoff fields
are amounts, not rates, and cannot by themselves close a three-hour interval.
Production water closure differences the cumulative fields at consecutive
timestamps and adds `water_aquifer` to the soil/snow/canopy/wetland stores.
`storage_gw` is retained as a diagnostic but is not added because it already
contains aquifer plus saturated-soil water. It is intended for bounded
multi-day to seasonal scientific pilots, normally at a coarser output
interval. Layered soil output activates HICAR's three-dimensional output path
and therefore carries the static height coordinate; budget that overhead for
pilots or replace it with a separately qualified sparse soil-checkpoint
product before an annual run. It is not the frozen 20-year production
contract. Qualification ranges use the raw static land mask for atmospheric,
snow, and surface-energy fields. Soil-state and runoff ranges additionally
exclude USGS water class 16 and permanent snow/ice class 24, whose Noah-MP
pseudo-soil state is saturated by construction; the class-stratified
diagnostic remains part of the validation record.
Snowfall and graupel are accumulated surface amounts in `kg m-2`, so their
finite/range checks use the same broad duration-independent corruption ceiling
as total accumulated precipitation. Event plausibility and phase partitioning
remain independent scientific diagnostics rather than fixed instantaneous
range limits.

The separate `wind_climatology` profile requests 10 m wind components plus
HICAR-native `u`, `v`, and air density at 50, 75, 100, 125, 150, and 200 m
AGL, together with friction velocity, momentum roughness length, surface-layer
bulk Richardson number, and boundary-layer height. The fixed-height fields use
linear interpolation in geometric height on the mass grid and never
extrapolate. The validator enforces coordinates, dimensions, metadata, finite
values, and broad physical ranges. The raw Richardson number may exceed the
internal value of 250 used to bound the MM5-revised similarity-function
inversion in calm stable cells, so that internal cap is not used as an output
validity bound. HICAR's `mol` field is deliberately omitted:
it is a temperature scale in kelvin, not Monin--Obukhov length. This profile
does not alter or include the land/energy `qualification` profile.

The fixed-height stream has passed its national engineering
restart/reduction/merge gate, but it is not yet the frozen long-duration
archive. It must feed composable interval statistics and selected-region
hourly products so the campaign does not retain routine full three-dimensional
output or duplicate speed, direction, and wind-power-density fields that are
exactly derivable from `u`, `v`, and density. ICON `VMAX` is excluded. Any
resolved HICAR interval maximum must carry exact time bounds and must not be
named a gust; a short-duration gust product requires a separately validated
HICAR-native method.

The current SwissMetNet wind channels `fkl010h0`/`dkl010h0` are hourly
means. A future gust gate must retrieve `fkl010h1`, the hourly maximum of
one-second wind, as a separate channel. It must explicitly harmonize that
one-second support with any HICAR three-second diagnostic; neither duration
may be silently relabelled as the other.

The committed-source 701 x 701 regional bridge passed this profile on two
Balfrin nodes with eight NCCL GPU compute ranks and two CPU I/O ranks in
2 minutes 42 seconds. A qualification-only run retained `u`, `v`, `z`, and
`density`; the bounded reference comparison passed at all six heights with
maximum errors below `9.5e-7 m s-1` for wind and `6.1e-8 kg m-3` for density.
The four surface/PBL fields passed metadata, finite-value and physical-range
checks. The 1,207,455,751-byte qualification file reduced to an
81,437,906-byte interval product containing the surface/PBL and marginal
direction/exceedance statistics, a 14.83-to-1 reduction. The durable record is
`../wind_climatology/validation/bridge_wind_surface_pbl_20100101.json`.

The stream runner now performs this bounded reduction immediately after the
raw wind profile and model/restart gate pass. A wind run must set
`STREAM_OUTPUT_PROFILE=wind_climatology` and explicitly choose
`STREAM_OUTPUT_INTERVAL`, because that value is the documented sampling
cadence of the resolved maximum. `STREAM_WIND_REDUCTION_INTERVAL` defaults to
86,400 seconds and must be both a multiple of the sampling cadence and an
exact divisor of the chunk. The reducer spans HICAR file boundaries, handles
restart continuations whose initial-boundary record is intentionally absent,
validates before atomic publication, and records hashes for every source and
the reduced product. For an explicit chunk boundary, it requires every raw
timestamp to be within one second of the inferred regular cadence and
constructs the exact canonical axis before grouping; inputs without an
explicit boundary retain strict regularity checks. The NetCDF and hash-bearing
JSON report receive distinct ready markers. Raw wind files may be retired only
through a separate hash-checked lifecycle action after both are published.
`retire_wind_source_output.py` is dry-run by default; `--execute` deletes only
raw files whose paths, sizes, and hashes agree in both the model-completion
and wind-reduction publications, while preserving the reduced product and
restart.

`scripts/merge_hicar_wind_statistics.py` then combines published interval
products in bounded horizontal blocks. With `--group-by month` it produces
calendar-month records; `--group-by all` produces one aggregate for a selected
contiguous period. The merge is exact for the current sufficient statistics:
sample-weighted means, pooled population variance, summed counts, and maximum
of the resolved maxima. It never reopens raw model output and publishes a
validation report plus separate ready markers for the data and report.
Retirement of the contributing interval products is dry-run by default and
hash guarded:
`retire_wind_interval_statistics.py --execute` removes only input NetCDF files
and their ready markers whose paths, sizes, and hashes agree with a published
merged report. It preserves the merged product and audit reports.

The two-hour Swiss national gate used four nodes with 16 GPU compute and four
CPU I/O ranks. The cold-start and restart-continuation hours completed in
9:29 and 10:05, with HICAR times of `417.54 s` and `486.96 s`; both model and
restart validators passed. The raw files were 1.111 GB for three records and
750.8 MB for the two continuation records. Their compact hourly products were
676.7 and 677.2 MB, and the exact four-sample merge was 682.7 MB. The
retirement dry run verified both interval hashes and reached
`READY_TO_RETIRE`; it deleted nothing. The durable record is
`../wind_climatology/validation/national_wind_stream_20100101.json`.

When the four surface/PBL inputs are present, the reducer requires the complete
set and publishes interval means of friction velocity, roughness, Richardson
number, and boundary-layer height plus interval maxima of friction velocity
and boundary-layer height. The merger composes these means by sample count and
the maxima by `max`. Older fixed-height streams with none of the four fields
remain readable; partial diagnostic sets are rejected.

The same pass publishes additive distribution counts at 10, 50, 75, 100, 125,
150, and 200 m AGL: 12 non-calm 30-degree meteorological wind-from sectors,
calm counts below `0.5 m s-1`, and counts at or above `3`, `5`, `10`, `15`,
`20`, and `25 m s-1`. Direction plus calm counts are checked against
`sample_count`; speed-threshold counts must be nested. These support national
wind roses and exceedance hours. They do not preserve chronology, persistence,
joint speed-direction frequencies, or an exact turbine-curve AEP.

For user delivery, `scripts/derive_hicar_wind_user_products.py` adds
wind-from direction of the vector mean, coefficient of variation, and
50--100/100--150/100--200 m shear and veer to any published compact interval
or merged product. This is a deterministic supplement, not extra HICAR state.
It explicitly masks calm/undefined ratios and does not claim that a direction
of vector means is the circular mean direction of all samples.

HICAR does not currently apply NetCDF deflation. The rolling workspace
therefore holds raw daily files, while the durable archive should contain the
losslessly verified compressed copies. Compression throughput on a complete
daily production file remains a live qualification gate; it must keep pace
with the GPU chains before production concurrency is increased.

## Paired 72-hour event gate

Summer and winter event promotion is evidence-based rather than status-only.
Each event must publish exactly 25 three-hourly model/station times, passing
model provenance bound to the repaired source commit, complete solver and
physical-budget reports, and the frozen station non-catastrophic-degradation
screens. The pair must be exactly one summer and one winter event, and both
model and station time axes must match their frozen start through hour 72;
record count alone is insufficient. The physical-budget, REA-L, SwissMetNet,
and OGD reports must each identify that event's exact model chunk. Each OGD
report must contain at least two complete 06--06 UTC RhiresD windows, three
complete 00--24 UTC TabsD days, and 24 matched SIS times.
After each model completes, a bounded `pp-short` audit independently checks
the 24-, 48-, and 72-hour restart files for canonical time, qualified schema,
repaired source identity, and whole-file SHA-256. The paired verdict depends
on both seasonal audits and requires exactly those three passing boundaries.
An additional early trajectory gate reuses the already-published summer
forcing by verified reference, independently restarts at hour 48, and compares
all eight three-hourly qualification records through hour 72 with the
continuous event. It performs no additional FDB retrieval or fieldextra
conversion. This event-scale screen can catch restart defects early, but it
does not replace the month-stage multi-day overlap.
Its interior TabsD temperature RMSE may exceed REA-L by at most 2 K; interior
RhiresD precipitation must satisfy
`HICAR RMSE <= max(2 * REA-L RMSE, REA-L RMSE + 2 kg m-2)`. Sparse or
catastrophically degraded reference reports cannot authorize the month or
100 m capacity stages.

## Scientific month pilot

The 31-day July 2020 pilot is a gated qualification workflow, not an
unconditional production launch. After the paired summer/winter assessment
publishes `GO_MONTH_AND_100M_CAPACITY_GATE`,
`prepare_scientific_month_pilot.py` creates immutable 7/7/7/7/3-day forcing
plans, a shared rolling-restart contract, and an eight-day uninterrupted
reference starting from the first seven-day restart. The declared spin-up is
the first seven days, leaving 24 retained days and 249 unique three-hourly
qualification records. The scientific plan, month plan, submitter, model
runner, and final assessor pin every main and overlap model job to a qualified
month-stage child of the preserved event commit; a clean but different
checkout cannot enter or pass the month gate. Month submission remains
blocked until a checksum-frozen output-diagnostic-only source qualification
proves parent ancestry, a clean build, nonzero restart continuity, bridge and
national short runs, and exact equivalence of all pre-existing fields and
solver gates.

The uninterrupted run crosses the July 15 segment boundary and supplies at
least 24 post-boundary hours. `compare_segmented_to_uninterrupted.py` compares
all qualification fields at every three-hour record using the tolerances
frozen in `config/scientific_pilot_plan.json`; equality only at the restart
instant is insufficient. The comparison must include precipitation and all
three new cumulative water observables.

`submit_scientific_month_pilot.py` is dry-run by default. Its 50-job Slurm DAG
starts the 249-record REA-L reference array and one whole-month SwissMetNet
retrieval beside segment-one forcing, overlaps bounded four-worker forcing
production with the active GPU segment, runs all model segments sequentially,
audits every wind solve before allowing its successor, compresses each raw
qualification file, retires each hash-verified forcing payload after its model
output and exact-end restart publish, retires superseded main-chain restarts
only after their successors and required overlap comparison pass, and blocks
segments four and five unless the
uninterrupted restart-trajectory comparison passes. After segment five, four
independent jobs evaluate whole-month physical budgets, REA-L consistency,
SwissMetNet skill, and OGD gridded skill. A post-spin-up screen flags only
large, nearly monotonic class tendencies; a signed scientific attribution is
required before any flag can be accepted.

Each model publication made by the current runner binds its output and restart
to a pre-run HICAR tree with no tracked changes and no untracked build inputs
under the declared source/configuration paths, a full source commit,
executable SHA-256 checked both before and after execution, exact archived
chunk plan and forcing publication, static-domain SHA-256, and model-log
SHA-256. Unrelated untracked Slurm logs or validation artifacts are permitted.
Before launching HICAR, the runner checks that its selected model validator
accepts every output, restart, and provenance option the runner will pass.
This turns a stale runner/validator interface into a preflight failure instead
of a post-simulation publication failure.
The month and annual assessors require this production-provenance block to
pass and require every model in a gate to share one source commit, executable,
and static-domain identity. Older event
publications remain usable as engineering/science evidence, but their legacy
completion schema cannot promote a month to annual execution or an annual
cycle to production.

The final assessor applies the frozen month-to-annual thresholds to exact
249-record coverage, every model/solver/compression/forcing-retirement and
restart-retirement publication, the restart
trajectory, the four scientific reports, drift attribution, and the production
archive and application-quality contracts. It can publish `GO_ANNUAL_CYCLE`,
a diagnostic or contract hold, or `STOP_AND_REDESIGN`; it never authorizes
20-year production. The current archive contract and application-specific
absolute quality/weight contract are intentionally `UNRESOLVED`, so an
otherwise passing month remains on a qualification-contract hold until both
are approved. Archive approval requires a durable non-scratch destination,
owner/quota, measured transfer, and restore drill.
The current `msclim` tape plan is smaller than the estimated 200 m compressed
campaign output plus checkpoints, so the visible Balfrin archive path is a
candidate tier rather than an authorization to publish.

`--execute` is the explicit launch action and writes a checksum-bearing
submission receipt plus ready marker. A partial submission journal prevents a
blind resubmission after any mid-DAG submission failure. Raw output retirement
remains forbidden until the scientific reports and lossless compressed
publications have been collected into an archive manifest.
Before either dry-run output or submission, the submitter now verifies the
production-provenance semantics of the runner, model validator, and month
assessor and records the path, size, and SHA-256 of every referenced Slurm
script plus those critical Python runtime files. A stale critical remote stack
therefore fails before the first `sbatch`.

## Annual scientific decision

The hydrological annual qualification is 2019-10-01 through 2020-10-01:
366 days and 2,929 unique three-hourly records. Its public-reference contract
expects 366 TabsD days, 365 complete RhiresD 06--06 UTC windows, 2,927 matched
SIS times, and 2,929 station model times, with at least 95% coverage in every
DJF/MAM/JJA/SON subset. The OGD retriever accepts
`OGD_PERIOD_START`/`OGD_PERIOD_END_EXCLUSIVE`, publishes both calendar-year
surface assets and all intersecting monthly radiation assets, and rejects a
ready manifest for a different period. Station and gridded comparators emit
seasonal metrics in addition to their unchanged whole-period/event results.

After a published `GO_ANNUAL_CYCLE`, approved archive and application-quality
contracts, and a new REA-L land initialization for 2019-10-01,
`prepare_scientific_annual_pilot.py` can publish 53 sequential 7-day/remainder
segments, four season-representative restart overlaps, two 28-day
independently initialized DJF/JJA overlaps, and the annual validation-source
contract. The generated plan carries the exact repaired HICAR source commit
from `config/scientific_pilot_plan.json`, and the annual assessor requires
every segment provenance block to match it. Its Balfrin wrapper is
planning-only and submits no computation.
There is deliberately no annual submitter until that publication and its
allocation, archive, diagnostic volume, and failure-recovery design are
reviewed after the month result.

Annual promotion additionally requires four segmented-versus-uninterrupted
restart boundaries, independently initialized DJF and JJA overlaps with at
least 21 retained post-spin-up days, zero unexplained drift flags, the frozen
absolute application-quality evaluation, a measured failure-recovery drill,
an approved archive transfer/restore, and one immutable production release.
`assess_scientific_annual.py` checks those publications and can issue
`GO_20_YEAR_200M_PRODUCTION`, a production-contract hold, a diagnostic hold,
or a redesign stop. Its authorization is limited to 200 m; it never promotes
100 m science. The release is evidence only when its published source
snapshot, executable, static domain, configuration bundle, and annual-plan
copy exist under the approved destination, match their declared SHA-256 and
sizes, and agree with the annual segment identity; the archive transfer must
also expose a verified published manifest at that destination.

## Readiness gates

- A live 200 m segment must pass output and restart validation, and a
  continuation must read that restart. `compare_restart_boundary.py` compares
  all routine diagnostics at the shared timestamp; a final qualification
  should also compare the segmented trajectory to an uninterrupted overlap.
- A live 100 m capacity/restart benchmark must fit `normal`, remain within GPU
  memory, and replace the current scaling estimate.
- The REA-L-derived land/snow initializer must pass summer and winter event
  pilots, restart-versus-continuous overlap, and an annual-cycle comparison
  before independent year chains are accepted.
- The final diagnostic list, temporal frequency, compression, retention, and
  archival destination must be frozen. Scratch is a rolling workspace, not
  the 20-year archive.
