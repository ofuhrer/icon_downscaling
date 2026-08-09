# ICON-to-HICAR scientific assessment

## Current conclusion

**The replacement national 20 m / 12 m setup is not campaign-ready.** Four
successively narrower interventions against the RRTMGP/OpenACC LW mapping stack
(`7729e124`, `fb4d811b`, `bc9dc1ac`, and `6071058f`) plus globally synchronous
execution all failed normal-cadence one-hour A/A repeatability. The final
candidate still differed at 60 minutes in 19 evaluation fields, with `lwtr`
again directly affected. The launch was therefore withheld: no daylight
restart proof, four-season integration, or new all-station skill claim was
made. A final call-level discriminator changed the fault classification without
changing that stopping decision. At the first differing LW call, raw HICAR
inputs were bit-identical while RRTMGP outputs differed on exactly one
32-column GPU-lane block per affected rank. A 32-column snapshot replayed 100
times in a standalone one-GPU RRTMGP driver was bit-stable both compactly and
after reconstructing the original rank width; CUDA memcheck and racecheck found
no errors or hazards. The defect is therefore most likely in HICAR's OpenACC
data ownership/lifetime or caller--RRTMGP handoff, not standalone RRTMGP
numerics. This stopping conclusion supersedes the historical investigation
detail below.

The repository is now oriented around one R&D path. The first direct-input
pilot found an unstable cached RBF stencil that created isolated 320 m s-1
winds; the remap is corrected and two-hour direct-input runs are finite across
the hourly turnover. Restart testing separated two effects. HICAR's tolerant
end check skipped the final roughly 0.77 s physics step of a terminal hour; it
did not take an extra step. Restricting that tolerance to exact flow events
(`def9723d`) makes terminal and continuing states at 01 UTC bit-for-bit equal.
Reverting the terminal-forcing reader change while supplying an extra forcing
record reproduced the skipped step, so LBC endpoint interpolation was not the
cause. The remaining restart perturbation came from rebuilding Noah-MP soil
geometry with an algebraically equivalent but differently ordered
single-precision expression. Matching the cold-path operation order
(`16de4b84`) makes both 60-second and one-hour segmented controls bit-for-bit
equal to uninterrupted runs across all 196 model-core restart variables.
The completed 65-site Alpine-bridge four-season campaign shows that the
corrected setup is numerically campaign-capable but does not provide general
added value over REA-L-CH1. Across the four 24-hour events, pooled HICAR RMSE is
worse by 38%
for height-adjusted 2 m temperature, 15% for relative humidity, 30% for 10 m
wind speed, 15% for the wind vector, and 10% for precipitation. The largest
wind degradation is over ridges and above 3000 m. This is ready for focused
R&D interventions, not for a 20-year production choice or a claim of
scientifically sound added value.

The stopped national follow-up was deliberately broader than the 65-site bridge
subset. Its staged observations contain every measurement-site key returned by
the broad Switzerland-covering SMN DWH query: 157 station abbreviations in each
event, 170 distinct abbreviation/measurement-site keys in the union, and 166
keys in the four-event intersection. This is query-relative completeness, not
an independent comparison with an official historical station inventory. No
queried site is excluded by the HICAR domain. Metric availability is narrower:
the complete four-event intersections are 145 keys for temperature and
humidity, 147 for wind speed/vector, and 132 for precipitation, and final
statistics must retain these metric-specific counts. The 2061x1431 grid has a
nominal 20 m lowest layer,
an achieved 15.956 m minimum interface thickness, and a hard 12 m acceptance
floor. Continuous and hourly-segmented two-hour pilots both passed their runtime
validators, but exact comparison of their terminal restart states failed: 108
of 196 variables differ, including maximum differences of 0.946 K in potential
temperature and about 0.23 m s-1 in horizontal wind. Persisted land and
radiation phase offsets differ by 0.00399971 s. A separate cold two-hour run
with a one-hour checkpoint was bitwise identical to the one-hour segment at
the initial output, then differed in 21 of 30 evaluation variables at one hour
even though both used the checkpoint/slab output path. The first divergence
therefore occurs before any restart is read; the phase offset is downstream,
not evidence of an initiating clock defect. A controlled non-SMP-aware MPI
reduction still diverged, so collective ordering is not the repair. One cause
was an uninitialized Noah-MP scratch `snow_nlayers` array read by the active
cold-start snow-temperature override: unused extra forcing/LBC metadata changes
allocation history, then the bad snow-layer count changes the land state. The
one-hour checkpoints differ in 103 of 196 variables and `snow_temperature`
differs by up to 273.16 K, matching that mechanism. HICAR `eff54862` now reads
the initialized prognostic snow-layer count instead. That removes the enormous
snow-temperature signature, but the corrected different-metadata cold controls
still diverge after one hour in 18 of 30 evaluation fields (soil temperature
maximum 0.000519 K, 2 m temperature 0.0504 K, PBL height 1358 m). More
decisively, repeating the *same* normal configuration with the same binary and
same ordered 12-node allocation is not bit-reproducible: the initial output is
exact, while the one-hour output differs in 21 of 30 evaluation fields. The
largest direct land differences are 0.00952 K in soil temperature and 0.1509 K
in surface temperature; subsequent atmospheric differences include 0.0813 K
in 2 m temperature and 1106 m in PBL height. Time, LSM/radiation cadence state,
wind elapsed time, U/V, pressure, Exner pressure, surface pressure, and active
forcing state are exact. This A/A failure proves that neither an extra timestep
nor the unused endpoint forcing/LBC record initiates the remaining mismatch;
the active national NVHPC/OpenACC path contains a timing- or ordering-sensitive
land-state calculation. Making Noah-MP's canopy `IndexShade` selector private
to each OpenACC gang (`7d84a960`) made a same-input two-record A/A pair exact
across all 196 model-core restart variables, but the 2-record versus 3-record
control still differed in 21 of 30 evaluation fields. Giving both cold starts
the same three-record lists does not close the problem: two sequential runs on
the same ordered 12-node allocation are exact at initialization but differ
after one hour in 18 of 30 evaluation fields. Their restart-resident land state
also differs directly: canopy temperature at 2,229 cells (maximum 0.08456 K),
ground-surface temperature at 1,850 cells (0.03882 K), soil temperature at 85
cells (0.001892 K), and soil water at 47 cells (8.94e-7), while the Noah-MP
timestep counter and phase offset are exact. This proves that the remaining
failure is genuine localized Noah-MP/OpenACC non-repeatability before restart,
not an extra timestep, LBC interpolation, output-only recomputation, or cadence
state. Compiler-wide synchronous OpenACC execution does not repair it: the
same-three-record A/A control still differs in 21 of 30 outputs and directly in
canopy temperature at 37,031 cells, ground-surface temperature at 24,561, soil
temperature at 2,389, and soil water at 2,128, while cadence state remains
exact. This supports an in-kernel vector-lane race rather than missing waits
between kernels. A
broader standards-motivated loop-private Noah-MP patch (`e5d01a16`) also failed
its same-input A/A test and was rejected. Source tracing shows that no future
regular-forcing payload is read and that sparse LBC only validates metadata for
unused future frames; those frames alter I/O/allocation timing, not the intended
physical state. The endpoint response therefore exposes residual ordering
sensitivity rather than an LBC-interpolation side effect. Source audit traced
the remaining defect to upstream Noah-MP `9e293596`, which fused five separate
vegetated surface-energy kernels into one long-lived OpenACC parallel region:
scratch declared private there is per gang, then shared by vector lanes. Clean
candidate `311e2262` reverses exactly that six-file execution-organization
commit while retaining all later Noah-MP driver/glacier/restart fixes and
leaving the physical equations, options, and intended operation order
unchanged. Its release/NVHPC/NCCL build is complete; three cold A/A replicates
now match bit-for-bit pairwise at initialization and after one hour in all 30
output variables and all 196 model-core restart variables on the same ordered
12-node allocation. This rejects the residual cold-start nondeterminism only
for that exact three-record configuration and source/build/topology. A first
25-record continuous control is identical to the three-record control at the
initial output but differs after one hour in 21 of 30 output fields, including
large land-state differences. That comparison also changed the requested end
time and Slurm allocation, so it is a useful race detector but not clean
evidence of list-length dependence. Reader tracing proves that future records
contribute metadata/support validation and allocation timing only, not future
atmospheric payloads or intended first-hour state. The terminal run also takes
no extra physics step: its final control pass occurs after the one-hour state
was packed.

A second source audit found the same persistent-region defect in the
bare-ground surface-energy path, introduced by Noah-MP `9a252eac`; it is the
only remaining long-lived OpenACC parallel region enclosing gang-vector loops
in the pinned Noah-MP source. HICAR `8a019da8` therefore also restores separate
bare-ground kernels in exactly the three affected execution-organization
files. A follow-up runtime/source check rejected a separate option-3 concern:
the namelist machinery's effective default is already `nmp_opt_sfc=1`; it
changes to 3 only when the user explicitly supplies `-1`. The prior national
runs therefore used supported Noah-MP MOST option 1, as confirmed by the nearly
identical first-hour flux fields when option 1 is written explicitly.
Coordinator `f283aef` pins that existing effective choice to remove the
ambiguity; this is configuration provenance, not a physics intervention. Its
clean NVHPC/NCCL build is complete. The first corrected-runtime repeatability
job completed one replicate and was then pre-empted during the second; the
other discriminators were canceled when a forcing-geometry defect was found.
They therefore establish neither failure nor exactness and must be rerun with
the repaired campaign inputs. No national seasonal model chain may launch
until the repaired-input A/A and continuous-versus-segmented controls pass. No
national added-value result or national restart-equivalence claim is yet
established.

The active path is:

`native REA-L GRIB -> hicarprep target state + sparse LBC -> HICAR -> paired
station/valid-time comparison with native REA-L`

The old fieldextra forcing path, pressure/wind certificates, publication
states, release gates, recovery bundles, and generalized production campaign
machinery have been removed. Runtime checks now protect only input identity,
chronology, dimensions, finite values, water representation, safe Slurm use,
and restart completeness.

## Selected experimental setup

| Component | Current choice | Assessment |
| --- | --- | --- |
| Grid | 200 m, 2061x1431 national domain | Covers all 170 available SwissMetNet measurement-site keys with a 40 km outer margin; the two-hour pilot establishes workable 12-node GPU resource use |
| Vertical grid | 80 levels, 12 km top, nominal 20 m first layer, stretch 0.65; hard 12 m thickness floor | National minimum interface thickness is 15.956 m, minimum mass-level spacing 16.642 m, and minimum mass Jacobian 0.17183; no layer is below the requested 12 m lower bound |
| Surface exchange | revMM5 atmospheric surface layer; explicit Noah-MP MOST resistance option 1 | This pins the previously effective default; option 3 is not active and the explicit line is provenance, not a physics change |
| Boundary terrain | 30 km cosine blend from REA-L-CH1 HSURF at the edge to the high-resolution DEM inward | Verified directly: edge weights are exactly zero, the transition ends at 29.8 km, terrain equals driving terrain on zero-weight cells and the high-resolution DEM on unit-weight cells, and the stored blend differs from the formula by at most 0.000131 m. It is deliberately broader than the separate 10 km atmospheric LBC shoulder, and all evaluated stations are inward of both zones |
| Terrain coordinate | SLEVE 2/6; smoothing window 5, 10 cycles | Previously stable; retain for controlled comparison |
| Wind | Adjoint variational, `Sx=true`, 500 m smoothing, alpha 1, 2500 iterations | Best-supported current option |
| Dynamics | RK3, density advection | Explicit and internally consistent |
| Atmospheric input | Hourly native REA-L decoded and transformed by hicarprep | Spring and autumn are complete and independently edge-validated; summer stopped after 18 complete hours and winter was not produced once the runtime failed repeatability |
| Moisture | Dry-air mixing ratios; QI explicitly zero only because source is absent; W diagnosed by HICAR | Explicit and interpretable; W remains a sensitivity |
| LBC | Hourly sparse hicarprep states, 10 km shoulder | Usable provisional choice; width is not closed |
| Land initialization | Native TERRA soil state plus ICON SWE/depth/density and bulk `T_SNOW` transferred to NoahMP | Integrated starting point; glacier snow is preserved and sourced vegetation climatologies are wired, but the winter response and any supplied climatology still need controlled tests |
| Radiation | RRTMGP, 600 s update; terrain radiation off | Blocked: the 12-node NVHPC/OpenACC path is not A/A repeatable; snapshot replay points to HICAR-side data lifetime/handoff rather than standalone RRTMGP numerics, and no campaign baseline is selected |

The forcing renderer now accepts only this configuration plus three output
profiles. It requires one static domain, four soil layers, matching hourly
forcing/LBC records, a list that brackets the segment, and explicit restart
input. The national campaign deliberately reuses one identical full-season
list in both segments to control the demonstrated metadata-order sensitivity.

## Critical input findings

- The old sparse-LBC evidence was not a clean validation of the new hicarprep
  atmospheric route: its initial state originated in the old fieldextra path.
  Certificate labels hid this dependency. Those labels and that route are gone.
- The direct hicarprep transform is conceptually sound: strict native P/T/U/V/
  QV/QC/W/HHL/HSURF/FR_LAND decode, explicit missing-QI policy, scalar and
  vector target weights, target-column vertical reconstruction, dry-water
  conversion, and paired forcing/LBC output.
- The active direct hourly producer decodes from the daily 00 UTC REA-L cycle,
  transforms one valid time, validates the target record, and publishes only
  the paired ready markers needed by concurrent readers.
- The native land packager no longer imports the retired remapper. SMI is a
  model choice, not a certification result. Static-epoch back extrapolation is
  still an acknowledged approximation and should be quantified if land-state
  errors dominate.
- Snow initialization transfers instantaneous ICON `W_SNOW`, `RHO_SNOW`, and
  `T_SNOW`. Hicarprep remaps SWE and derived snow volume separately with
  nonnegative, same-surface weights, diagnoses target depth and bulk density,
  and remaps bulk snow temperature on snow support with conservative skin-
  temperature bounds. HICAR uses that bulk value in active NoahMP snow layers
  only on cold starts; restart snow temperatures remain prognostic. The pinned
  NoahMP driver now preserves caller-provided SWE and depth on configured USGS
  ice category 24 rather than erasing glacier snow. This path is unit-tested
  and compiles locally, but still needs the staged two-hour winter diagnostic
  before snow or surface-energy improvement is claimed. Fractional snow cover
  and elevation-dependent redistribution remain deliberately unimplemented.
- The WorldCover path already reclassifies every 10 m pixel before 200 m area
  aggregation and retains all USGS category fractions; replacing that mapping
  is not the first land-cover intervention. Runtime-domain `VEGFRA`, `LAI`,
  `ALBEDO`, and maximum vegetation fraction are now range checked, normalized,
  and wired into HICAR when supplied. Twelve-month `VEGFRA` uses HICAR's native
  monthly path; the other climatologies are materialized at the initial valid
  time. No climatology is fabricated: absent explicit fields, HICAR retains its
  LAI 1, vegetation fraction 60%, and maximum vegetation fraction 80% defaults.
  A sourced `VEGFRA`/`LAI` sensitivity remains the next interpretable land-cover
  test.
- The independent preprocessing audit found real launch defects. The renderer
  now resolves `NZ` and `ADVECT_DENSITY`; the forcing/LBC pair is validated
  before either ready marker; forcing caches are season/domain-specific; and
  runtime completion checks now require exact timestamps, selected physics,
  all forcing brackets, and an exact terminal restart. The rendered national
  pilot namelist has `nz = 80`, `advect_density = .True.`, and no unresolved
  `@...@` tokens. Both its regular and LBC records embed the same runtime-static
  SHA256, so season-specific land states cannot alias by valid time alone.
- Sparse LBC U and V now always have distinct face-grid point sets, dimensions,
  weights, and bounds checks. Their values are reconstructed by midpoint
  interpolation from the mass-grid wind used by the regular forcing reader.
  The real national LBC has 345,780 mass supports and 345,882 supports on each
  face grid; the U and V index pairs differ at 343,821 positions and are unique
  within each support. This matches the structured-grid staggering convention
  but insertion still occurs after the wind projection and can temporarily
  perturb discrete continuity.
- Static construction now partitions the public-source product and appends
  HHL/HFL before publication. The first rebuilt Alpine-bridge grid exposed an
  important weakness in the nominal-level setting: its true SLEVE minimum was
  only 12.509 m. A later conservative bridge used a nominal 26 m first layer
  and achieved a 20.768 m minimum, but that is historical evidence rather than
  the selected setting. The active national grid uses the requested nominal
  20 m first layer with a 12 m hard floor and achieves 15.956 m minimum
  interface thickness, 16.642 m minimum mass-level spacing, and 0.17183
  minimum mass Jacobian. The HICAR namelist must use the same nominal setting
  because HICAR reconstructs SLEVE at runtime; the renderer checks the static
  geometry parameters, endpoints, and actual thickness before writing it.
- Native ICON HHL is ordered correctly, but unconstrained RBF interpolation of
  each interface independently crossed one layer in four of 491,401 target
  columns (worst thickness -123.6 m). Hicarprep now remaps the column endpoints
  and positive layer thicknesses, enforces a 20 m source-geometry floor, and
  redistributes only the thin-layer deficit while preserving the endpoints.
  The first real 00 UTC forcing/LBC pair passed with thickness rescaling limited
  to 0.9904--1.0023 and completed in 9:59 (job 5042543).
- That first target record was nevertheless invalid. One nearly singular
  ten-donor scalar RBF stencil had normalized weights as large as -204..206 and
  L1 amplification 897. It created three adjacent interior wind-spike columns
  44.4--44.6 km from the boundary: maximum vector speed 319.7 m s-1 at about
  777 m AGL, despite native U/V ranges of -20.3..94.6 and -59.4..35.4 m s-1.
  The sparse boundary happened not to include the bad interior columns, so its
  winds remained moderate and the previous finite-only forcing validator
  passed. Hicarprep now accepts only conditioned scalar operators, rejects a
  cached operator with L1 amplification above 10, maps scalar U/V within the
  local donor extrema, and rejects target wind speed above 200 m s-1. The
  rebuilt real operator needed a maximum 1e-8 nugget and reduced the maximum
  weight and L1 amplification to 1.65 and 6.49 (job 5042789). The seasonal
  cache must use the rebuilt operator; the old cache is invalid.
- The active national grid has 2,949,291 horizontal cells. A validated hourly
  80-level forcing record is about 3.5--3.6 GB and its sparse LBC about 1.4 GB;
  preprocessing peaks near 46--59 GB per record. The bounded controller runs at
  most six such jobs concurrently because pp-long does not account requested
  memory when packing jobs. The two-hour national pilot measured model
  throughput before the four-season campaign was permitted to proceed.
- A model-top warning exposed a real hicarprep geometry mismatch. The
  transform copied authoritative static HHL but reconstructed HFL as the
  arithmetic midpoint of adjacent interfaces. HICAR's nonlinear SLEVE
  coordinate instead evaluates the mapping at the mass-level reference
  height. Sampled old records differ from static HFL by up to 0.435 m; at the
  top the forcing deficit is up to 0.0143 m and triggers upper-level clamping.
  Coordinator `40eec5f` now preserves static HFL exactly. A float32 emulation
  then showed that HICAR's own single-precision reconstruction can be one ULP
  (0.000977 m at the top) above the rounded static value in 202,503 columns.
  Coordinator `41c92c9` therefore raises only the serialized forcing top mass
  level by one float32 ULP; its verified cover relative to emulated runtime is
  0--0.001953 m in every column. The paired validator requires that explicit
  contract. Atomic migration copies and checksum-verifies each old record,
  replaces only regular/sparse geometry and their binding hash, validates the
  pair, and only then restores ready markers. All old spring/autumn ready
  markers were quarantined while this repair runs; expensive atmospheric
  interpolation remains reusable. Campaign diagnostics using the old geometry
  were retired.
- Corrected 00, 01, and 02 UTC forcing/LBC pairs all passed the paired real-data
  validator with the conditioned operator (jobs 5042798, 5042799, and 5042810).
  A continuous two-hour full-physics run at HICAR `5fc3c71b` then completed
  across the 01 UTC turnover with exact output/restart times and finite bounded
  core state (job 5042814). Its CFL timestep changed from 1.40 to 0.8758 s at
  turnover as diagnosed grid-relative vertical wind increased from about 64 to
  127 m s-1. This establishes numerical turnover, not physical realism.
- The corresponding one-hour plus one-hour chain also completed, but its
  terminal core state was not identical to the continuous run. With the
  baseline restart initialization, maximum one-hour differences included
  about 0.45 K temperature and 0.34 m s-1 grid-relative vertical wind, with
  larger local differences in cloud and land diagnostics. Restart output
  correctly omits the predecessor's already-written terminal timestamp; the
  validator now handles that without weakening the state checks. A focused
  experiment that suppressed the restart-time wind projection made the
  divergence much worse (roughly 30 K temperature and 91 m s-1 grid-relative
  wind in the active core), so that intervention was rejected and HICAR was
  returned to `5fc3c71b`. The failure is not resolved by simply skipping the
  projection. At that stage, initialization/cadence state around the forcing
  boundary remained unresolved; the following experiments quantified it.
- Subsequent controlled tests at `e89b3f0c` localized that mismatch. Changing
  `Sx`, forcing-list origin, restart interval, forcing-reader look-ahead, and
  the probed wind operator did not repair it. Instrumenting the old reader
  with the formerly required extra forcing record showed 3599.233 s of physics
  in a terminal hour versus 3600.001 s in a continuing hour: the tolerant end
  check snapped away the last fractional step. `def9723d` removes that check
  only from ordinary timestep increments while retaining it at exact flow
  events. Independent one- and two-hour runs are then bitwise identical at
  01 UTC. Sparse-LBC brackets and timestep sequences are identical, disproving
  the LBC-interpolation hypothesis.
- The remaining restart perturbation was localized after 60 s to nine Noah-MP
  soil, runoff, and groundwater fields; all atmospheric variables were exact.
  Two independent cold controls remained bitwise equal across all 196 fields,
  proving this was restart initialization rather than GPU non-determinism.
  Noah-MP's restart initializer rebuilt cumulative soil-layer depths with
  direct `DZS` subtraction, while the cold snow initializer formed the same
  depths through differences of cumulative `ZSOIL`. Their single-precision
  results can differ by one ULP. HICAR `16de4b84` patches the fetched, pinned
  Noah-MP source to use the cold-path operation order. A 60-second restart and
  the full 00--02 UTC continuous versus 00--01 + 01--02 UTC chain are then
  bit-for-bit equal across all 196 model-core restart variables. This is direct
  bit-reproducibility evidence for the tested two-node GPU topology, physics,
  forcing, and hourly checkpoint; it is not a universal compiler/topology
  proof.

## Reflected-shortwave cadence defect

The reported defect was real. `ra_driver.F90` rebuilt total shortwave from
direct plus diffuse every model step, while terrain-reflected shortwave was
added only on full radiation updates. With a 600 s radiation interval, the
reflected contribution disappeared between updates.

HICAR commit `7ad54787` caches and reuses the last reflected component between
full radiation calls, while excluding the stale value during a refresh before
computing the new component. The exact patch compiled with NVHPC/OpenACC on
Balfrin (job 5042469), and its targeted GPU unit test passed (job 5042480).
This proves the recurrence logic, not the full model energy budget. Terrain
radiation therefore stays off in the campaign baseline pending a short cadence
diagnostic.

## What previous stable integrations actually established

- The strongest full-state result is a six-hour Swiss 200 m run at HICAR
  `79c87b86`: 80 levels, 12 km top, full Morrison/YSU/NoahMP/revMM5/RRTMGP
  physics, adjoint variational wind, density advection, RK3, and CFL factor 1.6.
  Its minimum interface thickness was 12.257 m; all stored atmospheric fields
  were finite and plausible at the endpoint. It used the retired fieldextra
  regular forcing path, not direct target-native hicarprep.
- A 72-hour summer run at `5da4b198` used the same coupled configuration and
  completed with finite surface/land output, valid daily restarts, and passing
  solver diagnostics. Its timestep was recomputed at regular hourly forcing
  updates and varied about 3.44--3.77 s. It was numerically stable but
  scientifically poor: comparison to SwissMetNet showed a 3.66 K warm bias,
  14.3 percentage-point dry RH bias, near-zero modeled precipitation during an
  observed wet period, and weak 10 m wind correlations. REA-L was better on
  every headline metric in that event: HICAR versus REA-L RMSE was 5.12 versus
  1.44 K for 2 m temperature, 23.50 versus 10.48 percentage points for RH,
  2.55 versus 1.40 kg m-2 for interval precipitation, 2.27 versus 1.58 m s-1
  for wind speed, and 3.35 versus 2.37 m s-1 for the wind vector. Stability is
  not skill.
  Its daily restart files were checked for presence and finite state, not
  against a continuous control, so it did not establish restart equivalence.
- The earlier two-hour Storm Sabine sparse-LBC run at `5d557495` crossed the
  01 UTC bracket with finite fields using CFL factor 1.6 and dt 1.28 then
  1.03 s. It used the same 20 km ICON-topography blend as the current bridge,
  but it was a hybrid: its regular IC/hourly forcing was produced by fieldextra
  (specific humidity, supplied W, runtime relaxation), while only the sparse
  LBC came from hicarprep. It therefore did not validate the new direct route.
- The 33-hour 250 m prototype retained 32 finite hours but used only 40 levels,
  a roughly 143 m first level, wind='none', and no PBL/LSM/surface/radiation/
  water physics; it then aborted at forcing-list exhaustion. It is only a
  coarse dynamical-core smoke result.

These precedents show that density advection and CFL factor 1.6 can be stable;
they do not support treating a lower CFL as the primary repair for the current
pilot or claiming that the old restart files were trajectory-equivalent. The
requested vertical floor remains approximately 20 m for new work and must
never fall below 10 m; historical 12 m evidence is comparison evidence, not
the selected grid.

## Campaign and evaluation design

`orchestration/rd_campaign.py` is the only campaign controller. The current
four-season case uses two exact 12-hour segments per season, one serial restart
chain per season, independent seasonal chains, bounded retries, and the
preemptible partition for HICAR. The endpoint fix removes the former need for a
disposable overlap hour. Filesystem truth is limited to input ready markers,
Slurm job IDs, restarts, `segment.json`, and `segment.complete`. The campaign
record includes source commits and working-tree hashes.

The four-season experiment uses equal-duration, synoptically varied windows and
scores HICAR and REA-L against the same SwissMetNet sites, QC masks, and exact
common valid-time triplets. The active national comparison uses the nearest
HICAR cell and staged nearest native REA-L samples extracted from exact
observation rectangles; the maximum staged REA-L distance is 0.863 km and the
maximum HICAR-cell distance is 0.124 km. Its main statistical limitation is
that instantaneous HICAR values are compared with hourly station aggregates.
Reports need paired differences and uncertainty intervals before a claim of
added value is robust.

National verification remains deliberately small: primary equal-station RMSE,
signed bias, MAE, RMS station bias, and within-station centered RMSE. The exact
decomposition is network RMSE squared = mean squared station bias + mean
squared within-station centered RMSE; subtracting only the signed national-mean
bias is invalid when opposing persistent station biases cancel. Station-level
standard-deviation ratios and correlations are median/IQR diagnostics only,
with at least 20 hourly pairs per station-event and at least 10 stations per
displayed group. HICAR-minus-REA-L station differences and improved/degraded
counts preserve the paired comparison. Lead-hour traces are event-specific
elapsed-time diagnostics, not forecast-degradation curves, because one event
per season confounds lead with valid hour, diurnal evolution, and event.
Wind-speed bias/MAE and vector RMSE distinguish amplitude from directional
error without requiring a circular headline score. If circular direction is
shown diagnostically, its calm mask is now observation-only at 2.5 m s-1 so
model performance cannot change the verification sample (`40cf0c5`). Groups
with fewer than ten stations are named-site evidence, not aggregate skill; in
particular the old two-to-three-site >=3000 m class is descriptive only.

## Four-season campaign result

All eight 12-hour model segments completed on the `preemptible` partition on
their first attempts, using two nodes each. Every segment used 13 forcing and
13 sparse-LBC records and produced an exact terminal restart; first segments
stored 13 hourly times and continuations correctly stored 12 new times without
duplicating the restart timestamp. Wall times were 22--43 minutes per segment.
No NaN/fatal diagnostics occurred, and all segment validators passed.

The first comparison run exposed and rejected an evaluation error: the
SwissMetNet query included stations outside the 701x701 bridge, which had been
mapped to edge cells as far as 108 km away. Evaluator `a863cf4` now excludes
sites farther than max(1 km, three grid spacings). Each accepted seasonal
report has 25 matched hourly times, 65 in-domain stations, maximum nearest-cell
distance 0.703 km, and no reported issues. RMSE values below are HICAR / native
REA-L-CH1 against the same quality-controlled observations:

| Event | T2m adjusted K | RH %-point | Wind speed m s-1 | Wind vector m s-1 | Precip kg m-2 | Pressure Pa |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Winter | 4.05 / 3.12 | 19.90 / 15.93 | 2.39 / 2.12 | 2.87 / 2.81 | 0.003 / 0.003 | 128 / 163 |
| Spring | 1.91 / 1.24 | 15.64 / 13.05 | 1.84 / 1.66 | 2.70 / 2.60 | 0.403 / 0.363 | 124 / 163 |
| Summer | 2.99 / 1.43 | 15.32 / 11.22 | 1.64 / 1.52 | 2.44 / 2.54 | 1.782 / 1.266 | 120 / 152 |
| Autumn | 2.46 / 2.27 | 16.70 / 18.08 | 5.51 / 3.92 | 7.15 / 5.84 | 3.973 / 3.734 | 275 / 200 |
| Pooled | 2.96 / 2.15 | 16.99 / 14.81 | 3.25 / 2.50 | 4.26 / 3.72 | 2.186 / 1.980 | 174 / 171 |

The winter event was essentially dry, so its precipitation score is not
informative. HICAR did improve pressure in three events, autumn RH, summer
vector wind, and some wind subclasses, but these are not a general added-value
signal. Pooled boundary-zone vector RMSE was marginally better (3.57 versus
3.61 m s-1), as were wind speed below 500 m (2.02 versus 2.10 m s-1) and vector
wind below 500 m (2.85 versus 3.15 m s-1). In contrast, interior wind speed was
3.48 versus 2.55 m s-1 and interior vector RMSE was 4.47 versus 3.75 m s-1.
Above 3000 m, HICAR wind-speed/vector RMSE reached 9.67/11.85 m s-1 versus
3.90/6.93 for REA-L; ridge values were 5.96/7.65 versus 3.71/5.42 m s-1.
The next wind experiments should therefore target high-terrain/ridge behavior,
not increase horizontal resolution or campaign duration blindly.

Reprocessing the full diagnostic reports with station-aware error anatomy
materially narrows that interpretation. The apparent ridge/high-elevation
failure is overwhelmingly the autumn event, not a four-season invariant. Ridge
wind-speed RMSE was HICAR/REA-L 2.49/2.76, 2.72/2.62, and 2.39/2.47 m s-1 in
winter, spring, and summer, but 10.94/5.81 in autumn; the corresponding vector
values were 3.47/3.66, 4.46/3.89, 3.54/3.55, and 13.60/8.64 m s-1. Only 9--10
ridge stations contributed per event, and just 2--3 stations lay above 3000 m,
so this supports an event/station-local diagnosis rather than a generic ridge
claim. In autumn, wind-speed network RMSE 5.51 m s-1 decomposes into 4.15 m s-1
RMS station bias and 3.63 m s-1 within-station centered RMSE, versus 2.82 and
2.73 for REA-L: both persistent spatial/representativeness error and temporal
evolution matter. Above 3000 m the HICAR components are 14.69 and 10.54 m s-1,
but that three-station result is descriptive only.

The elapsed-hour tables also reject a generic lead-time degradation story.
National wind differences are nearly flat in winter, spring, and summer. In
autumn, HICAR-minus-REA-L wind-speed RMSE changes from -0.07 m s-1 averaged over
hours 0--11 to +2.64 over hours 13--23; ridge wind changes from -0.17 to +8.43,
with the transition visible across hours 11/12/13 (+2.46/+4.84/+7.04) and a
maximum +10.23 at hour 19. Vector wind shows the same event evolution. Because
the other three cases do not show a restart-hour discontinuity, this is more
consistent with the autumn storm/diurnal regime and the now-rejected
bare-ground GPU runtime than with restart or elapsed lead itself. Summer temperature and
spring humidity also worsen in their second halves, but with strong concurrent
bias changes; these remain valid-time/event effects until independent cases
repeat them. Legacy reports do not retain samples needed for correlation or
standard-deviation-ratio diagnostics, so those must wait for the national
campaign.

For the scalar fields, the added decomposition explains several otherwise
ambiguous RMSE changes. HICAR's pressure improvement in winter/spring/summer is
almost entirely smaller persistent station bias (RMS station bias about
109--120 Pa versus 145--159 Pa), while its within-station temporal error is
slightly worse (41--50 Pa versus 36--46 Pa). Autumn pressure is worse in both
components. Autumn RH improves overall because HICAR reduces RMS station bias
to 12.1 from 14.7 percentage points despite a slightly worse centered error
(11.5 versus 10.5). Temperature deficits in winter, spring, and especially
summer contain both larger station-bias and centered components; summer is not
merely a uniform warm offset. Summer and autumn precipitation errors are
dominated by within-station/event evolution rather than mean bias; in autumn
HICAR even has the smaller RMS station bias but the larger centered error and
therefore the larger total RMSE. These are useful hypotheses for the corrected
national rerun, not conclusions about seasonal climatology.

These are exploratory scores, not confidence bounds: four single-day cases
do not estimate seasonal climate skill, observations are hourly aggregates
while HICAR surface fields are instantaneous, and station/time errors are
correlated. The campaign nevertheless rejects the current broad hypothesis
that 200 m HICAR already adds wind skill over REA-L-CH1.

## Decision sequence

1. Stop the current campaign path. Do not resume input preparation or launch
   the national seasonal integrations with the tested NVHPC/OpenACC RRTMGP
   runtime.
2. Resume only after an explicit new decision to investigate the remaining
   HICAR/OpenACC caller--RRTMGP data-lifetime defect or to qualify a
   scientifically acceptable alternative radiation/runtime path. Any
   replacement must first pass the
   same normal-cadence one-hour A/A test and then the 00--08 UTC daylight
   continuous/segmented restart proof.
3. Only after those proofs should the already-defined all-station seasonal,
   ridge/elevation, and lead/valid-time analyses be reconsidered. Do not test
   100 m or plan a 20-year run on the present evidence.

## Verification status

- Coordinator tests: 136 passed.
- Repository syntax/policy checks: passed.
- The remaining 12-node GPU nondeterminism is isolated to the HICAR/OpenACC
  context around repeated RRTMGP execution, not restart I/O, the LBC endpoint,
  or the standalone RRTMGP calculation. Diagnostic HICAR `44d434da` hashed all
  raw LW inputs and broadband outputs per rank and call. In a clean replica
  pair, the first difference was call 3 at 10 minutes on global ranks 1, 17,
  and 36; inputs matched exactly, while each affected rank differed in exactly
  one contiguous 32-column block (rank 1 columns 15937--15968; ranks 17 and 36
  columns 97--128). A targeted rank-17/columns-97--128 snapshot
  (`4c8a5c54...0462d47f`) was replayed against the same patched RRTMGP libraries
  on one GPU. All 100 compact repetitions shared one output hash, and all 100
  original-width reconstructions shared one output hash. CUDA memcheck reported
  zero errors in both geometries and racecheck reported zero hazards in the
  compact case. Evidence is under
  `$SCRATCH/icon_hicar/rd/swiss_all_stations_20m/diagnostics/rrtmgp_trace_7ed54cd9_phase3`,
  `rrtmgp_snapshot_44d434da_rank17_c3_cols97_128`, and the two
  `rrtmgp_replay_44d434da_rank17_c3_cols97_128_*` directories. This is strong
  evidence for HICAR-side mapping/alias lifetime or handoff sensitivity; it is
  not evidence for a self-contained RRTMGP/NVHPC reproducer. Experiments stop
  here.
  With normal 600-second
  radiation, replicas first differ after two to four radiation updates in
  exactly 32 contiguous `lwtr` cells corresponding to one flattened GPU warp;
  surface/PBL and later dynamical fields then amplify the seed. Four replicas
  with only the initial radiation call (`update_interval_rad=7200`) were exact
  at every output slice and across all 196 terminal-restart variables. Stable
  gas-concentration mappings (`5372fc7e`) did not remove the failure. Explicit
  scalar privatization compiled to identical accelerator code and failed;
  direct lane-owned broadband accumulation (`121176e3`) changed the generated
  kernel but still diverged at 30 minutes and is rejected. Clean HICAR
  `7729e124` explicitly staged both RRTMGP LW flux arrays through host memory
  and remapped fresh plain arrays for both LW consumers. All four normal-cadence
  production-topology replicas completed, but A--B first diverged at 30 minutes
  and A--C/A--D at 50 minutes; the first seed remained one contiguous GPU-warp
  footprint of 31--32 `lwtr` cells.
  Host staging is therefore rejected and proves that the nondeterminism is
  already present in the RRTMGP fluxes or their inputs, rather than being only
  stale pointer visibility in HICAR's downstream LW consumers. Global
  synchronous execution of clean `5372fc7e` made A--B and A--C exact but A--D
  again first diverged at 30 minutes in `lwtr`; synchronization is rejected.
  Clean `fb4d811b` removed HICAR's outer mapping of the two LW broadband output
  arrays and retained a fresh host-stage/device-stage handoff. All four
  replicas were exact at 30 minutes, but A--B diverged at 50 minutes, again
  beginning in `lwtr`; outer mapping ownership alone is rejected. Clean
  `bc9dc1ac` additionally made the frontend's balanced enter/copyout pair the
  sole owner of caller broadband arrays while inner solver aliases used
  `present` and allocated only spectral scratch. A--B remained exact through
  50 minutes but diverged at 60 minutes, again beginning in `lwtr`; this deeper
  ownership cleanup is also rejected. The final mapping discriminator was
  clean `6071058f`, which removes the one-angle kernel's redundant
  enter/delete/copyout ownership of spectral scratch already owned by its
  caller. Its A--B terminal state still diverged at 60 minutes: 19 evaluation
  fields differed, including 52,401 `lwtr` cells (maximum LW-tendency
  difference 0.7407), with a maximum cross-field absolute difference of
  1121.93. The identified duplicate-mapping stack is therefore not the root
  fix; removing it only delayed the same failure. Normal-cadence 12-node GPU
  RRTMGP is not repeatable enough for scientific attribution, so the daylight
  proof and seasonal campaign were not launched. Per the explicit stopping
  decision, no further source discriminator is planned. Compact durable
  evidence is under
  `/store_new/mch/msopr/olifu/icon_downscaling/rd/national_20m_rrtmgp_stop_6071058f`:
  the terminal A--B comparison, clean-build provenance, exact HICAR source
  bundle, and static-geometry report.
  A matched CPU 10-minute A/A output test is exact, but the demonstrated GPU
  onset occurs later, so it is supporting architectural evidence rather than a
  one-hour CPU proof.
  The generic GPU unit-test executable still has an unrelated
  multi-device PRESENT failure and the CPU build exposes a pre-existing
  SNOWPACK real-constant overflow; neither occurs in the production topology.
- Four-season forcing/reference/observation preparation and all eight model
  segments are complete. The campaign ran from clean coordinator `cba8be6`
  and clean HICAR `16de4b84`; corrected evaluation used clean coordinator
  `a863cf4`. Minimal durable evidence is under
  `/store_new/mch/msopr/olifu/icon_downscaling/rd/four_season_24h_16de4b84_a863cf4`:
  four reports, campaign/build provenance, eight segment manifests, the
  bitwise restart comparison, and verified source bundles. Large trajectory
  data remains in scratch.
- Preparation for the replacement 20 m / 12 m national campaign stopped with
  complete validated spring and autumn records (25 forcing plus 25 LBC each),
  18 complete summer hours (36 ready markers), and no winter records. The
  controller and six active summer workers were canceled once the final GPU
  repeatability experiment failed; no partial published products remain.
