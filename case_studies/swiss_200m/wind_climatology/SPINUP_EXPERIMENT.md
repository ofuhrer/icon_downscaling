# HICAR wind spin-up experiment

> [!WARNING]
> This is the historical design for experiments run before HICAR commit
> `6bd302f8`, which restored advancement of the native horizontal wind state.
> Its convergence and hard-handoff conclusions are scientifically invalid for
> method selection. Reuse the cases, inputs, diagnostics, and comparison code;
> do not reuse its production decisions or require its staged gates during the
> current R&D phase. The active evidence and next experiment are summarized in
> `memory/project-assessment.md`.

## Decision to make

Determine the shortest discarded cold-start interval that makes a sequence of
independent daily HICAR simulations equivalent, for the wind-climatology
product, to a substantially longer-spin-up reference. This is a numerical
convergence design, not an observational-skill assessment. It is now retained
as experimental provenance, not as an authorization workflow.

The production candidate remains the qualified `feature/icon_downscaling`
solver baseline. Its fixed-height diagnostics are evaluated at output time
only. Physics must otherwise be identical across every member of the
experiment. Morrison is the selected baseline because it is the currently
qualified configuration and has acceptable throughput. Thompson is excluded
from this wind campaign because its measured throughput is operationally
prohibitive.

## Branch decision

- `feature/icon_downscaling` at `7700c97a…` contains the qualified
  decomposition, advection, and variational-solver corrections and is the
  production base.
- the unreferenced `2999c9bd…` commit contains the independently validated
  fixed-height output feature but predates those solver corrections; only that
  diagnostic feature is ported onto the production base as candidate commit
  `b5146a3c…`;
- `codex/v29-summer-warm-bias` is a sibling research branch with water-budget
  diagnostics and does not contain the complete qualified production path, so
  it is not used as the wind baseline;
- Thompson changes only the namelist physics on an immutable `7700c97a…`
  runtime. Its 24-hour gate was stopped after 5 h 50 min wall time because the
  throughput was operationally prohibitive; it is not part of this campaign.

## Output contract

The experiment and production profile use the already assessed
`wind_climatology` source stream:

- `u10m`, `v10m`;
- `u_agl`, `v_agl`, and `rho_agl` at 50, 75, 100, 125, 150, and 200 m AGL;
- `ustar`, `surface_roughness`, `sfc_Ri`, and `hpbl`.

This is the source-complete 24-field-equivalent stream in
`PRODUCT_CONTRACT.md`. Speed, direction, shear, veer, and wind-power density
are deterministic derived products and are not duplicated in raw output.
Full-level `u`, `v`, `w`, `density`, and `z` are qualification-only fields.

Resolved sample maxima are retained with their sampling interval. They are not
gusts. A HICAR-native `wind_speed_of_gust` may be activated only after the gust
gate in `PRODUCT_CONTRACT.md` passes against duration-matched Swiss
observations. The experiment must not copy ICON `VMAX`.

## Staged experiment

### A. Bridge-domain screening

Use the already qualified 701-by-701 bridge first. For every event, run
independent cold starts with discarded spin-up intervals
`0, 1, 2, 3, 6, 12, 24, 48` hours. Retain the same following 24 hours and one
additional overlap hour from every member. Use 30-minute output during this
screen so that convergence within the first retained hours is visible.

The event set must cover at least:

1. the existing summer convective case beginning 2020-07-01 00 UTC;
2. the existing winter stable case beginning 2020-01-15 00 UTC;
3. a strong-gradient or foehn case selected without reference to HICAR error;
4. a calm, stable nocturnal case selected without reference to HICAR error.

Select cases 3 and 4 from ICON REA-L-CH1 or observations using frozen,
documented criteria before inspecting the HICAR spin-up comparisons.
The first frozen screen uses Storm Sabine on 2020-02-10 as the independently
documented strong-wind event and the documented Swiss Plateau inversion on
2014-11-21 as the calm/stable candidate. The latter must pass the REA-L-only
calmness and stability gate in
`experiments/bridge_spinup_case_selection_v1.json` before HICAR differences
are inspected.

Compare every member over identical valid times with the 48-hour member.
The assessor selects the shortest member that passes in every event and whose
longer-spin-up tail also passes. A non-monotonic isolated pass is therefore
not accepted.

### B. Swiss-domain confirmation

On the 2,271-by-1,651 production grid, repeat only the selected bridge
candidate, the next shorter candidate, and the 48-hour reference for all four
events. Use the production hourly cadence. The candidate must pass at every
height and event; otherwise move to the next longer candidate and repeat.

### C. Stitched-chain confirmation

Run a 30-day daily cold-start chain using

`spin-up + 24 retained hours + 1 overlap hour`

and an otherwise identical 30-day restart-linked control. Compare:

- daily and monthly sufficient statistics;
- wind-direction and threshold counts;
- resolved block maxima;
- one-hour overlap values on both sides of every seam;
- selected SwissMetNet stations and any available mast or lidar sites.

No seam record is counted twice. The archive uses half-open ownership:
day `D` owns `(D 00 UTC, D+1 00 UTC]`; the following overlap is validation
only. Campaign drift is irrelevant only if this stitched-chain gate passes.

## Frozen convergence criteria

For each height and event, relative to the longest-spin-up reference:

- horizontal vector RMSE no larger than `0.20 m s-1` and no larger than 3% of
  reference RMS speed;
- absolute mean scalar-speed bias no larger than `0.10 m s-1`;
- mean direction difference no larger than 5 degrees where both winds are at
  least `2 m s-1`;
- 99th-percentile vector difference no larger than `0.75 m s-1`;
- no missing or non-finite fixed-height values.

The longest-spin-up member is a self-comparison and therefore cannot establish
convergence on its own. A minimum is bracketed only when at least one shorter
member and every longer tested member pass. If only the reference passes, the
decision is `MINIMUM_SPINUP_NOT_BRACKETED` and the reference duration is
reported only as a lower bound.

The stitched 30-day product must additionally reproduce monthly scalar and
vector means within `0.10 m s-1`, direction/threshold counts within 0.5% of
samples, and seam vector differences within the same instantaneous thresholds.
These criteria measure cold-start convergence. The separate observational
wind and gust gates remain mandatory.

## Operational interpretation

The pre-emptible controller can represent a 26-hour experiment as one 24-hour
attempt plus a final 2-hour attempt in the same restart chain. Use it when
bounded retries and data lifecycle reduce risk or waste. During R&D,
scientific conclusions follow from the relevant comparisons, not from
controller or publication status. Full production qualification is deferred
until the strategy has converged.
