# ICON-to-HICAR scientific R&D assessment

## Current mode and target question

The project is in **scientific R&D / strategy-discovery mode**. The target is
an evidence-backed answer to:

> What ICON-to-HICAR downscaling strategy should we use, why, under which
> conditions, and what important uncertainties remain?

Production qualification, generalized orchestration, immutable releases,
archive contracts, exhaustive provenance, and long-campaign hardening are
deferred until that strategy is scientifically convincing.

The primary application is wind downscaling from ICON REA-L-CH1 at 200 m and
possibly 100 m over Switzerland. HICAR remains fully coupled in the current
experiments; a wind-focused product is not a `wind_only` model configuration.

## Working scientific understanding

The best-supported provisional wind strategy is a sequence of independently
initialized, fully coupled 72-hour HICAR windows, discarding the first 48
hours and retaining one deterministic 24-hour core from the oldest eligible
window. Overlaps quantify origin-age uncertainty. Do not taper or blend
phase-shifted turbulent fields.

This is supported for one summer Alpine bridge case only. It is not a valid
strategy for surface temperature, soil, snow, water budgets, precipitation,
or a general continuous coupled trajectory. Those applications currently
favor restart-continuous integration unless later experiments identify a
better initialization or cycling method.

The current configuration starting point is the discretely adjoint
variational wind solver, 80 levels, 12 km model top, and SLEVE decay 2/6 on
the validated 200 m geometry. It is a tested baseline, not a frozen scientific
choice. The 60-level version is available as a cheaper control when vertical
resolution is not part of the question.

## Established engineering foundations

- HICAR commit `6bd302f8b97062cd43c1b8d4e59bd3cf0dc8ae07` restores application of
  the adjusted horizontal-wind tendency. Native `u`/`v` and every requested
  fixed-height wind evolve under nonstationary forcing.
- The corrected source passes the four-GPU halo tests and a cross-node
  split/restart comparison across all 193 restart variables and 13 tested
  output variables.
- The 200 m 80-level SLEVE 2/6 national geometry, four-node NCCL layout,
  forcing conversion, REA-L land initialization, fixed-height output, and core
  numerical diagnostics have representative evidence.
- Short restart-linked scheduler jobs can preserve a coupled trajectory. A
  seven-day repeated-day run completed as separate two-node jobs with all six
  handoffs valid.
- The pre-emptible controller, shared forcing cache, ready markers, rolling
  retirement, and durable manifests are available when an experiment benefits
  from them. They are infrastructure, not prerequisites for scientific
  acceptance.

## Latest experiment: chronological summer overlap

### Uncertainty and hypotheses

- H1: after 48 hours, independently initialized coupled windows reproduce the
  continuous wind/PBL trajectory closely enough for a wind product even when
  slow land state differs.
- H2: reset land/surface state materially contaminates wind after 48 hours, so
  the model must run as one continuous trajectory or use a different cycling
  strategy.

### Minimal discriminating experiment

On the 701 x 701 Alpine bridge, three daily-origin 72-hour windows and one
five-day restart-continuous reference used chronological 2020-07-01 through
2020-07-06 REA-L forcing, origin-specific REA-L land/snow initialization,
30-minute output, identical corrected HICAR source, and no blending. The 56
six-hour model segments all completed and published interpretable output.

### Result

H1 is supported for summer wind, while the same strategy fails as a general
coupled-state method.

- Both genuinely reset retained cores passed all predeclared wind and PBL
  thresholds. Against the continuous reference, 10 m vector RMSE was `0.0179`
  and `0.0269 m s-1`; 100 m RMSE was `0.00571` and `0.00670 m s-1`; 10 m
  timewise p99 error was `0.0467` and `0.0747 m s-1`.
- PBL-height relative RMSE was `0.0588` and `0.0631`, below the `0.15`
  diagnostic threshold. Wind seam-excess RMSE was at most `0.0207 m s-1` at
  10 m and `0.00449 m s-1` at 100 m.
- Reset slow state was material: soil-column-water mean bias was `-3.14` and
  `-7.46 kg m-2`, soil-temperature RMSE was `0.376` and `0.583 K`, and
  soil-water RMSE was `0.0111` and `0.0163 m3 m-3`.
- Surface-temperature RMSE was `0.735` and `0.947 K`; seam excess was `0.639`
  and `0.517 K`. The sensible- and latent-heat mean biases themselves remained
  small, so the causal path from reset stores to wind is not established.
- Same-valid-time origin-age uncertainty is larger than selected-core bias:
  pairwise 10 m wind RMSE spans `0.0787--0.1283 m s-1`. This is useful
  uncertainty information and argues for deterministic oldest-core ownership.
- The duplicate same-initial-state control failed an intentionally near-exact
  pointwise check, but its bulk differences were much smaller than the reset
  signal: 10 m RMSE `0.00308 m s-1`, surface-temperature RMSE `0.0278 K`, and
  soil-temperature RMSE `0.00338 K`. Rare localized nondeterministic extremes
  remain an engineering/numerical uncertainty; they do not explain the reset
  land-state differences.

The archived assessor status `PASS` means the analysis executed successfully;
its `RESET_STATE_BIAS_MATERIAL` decision correctly rejects a single method for
all coupled fields. During R&D it does not override the separate positive wind
result.

## Open scientific choices

| Choice | Current evidence | Smallest next discriminator |
| --- | --- | --- |
| Independent 72 h windows for wind | Supported in one summer bridge case | Repeat the same causal comparison in a stable-winter regime |
| Continuous versus reset coupled state | Reset soil and surface temperature are materially different | If coupled products matter, compare continuous integration with a targeted land-state cycling or longer-warm-up intervention |
| Warm-up length | 48 h is sufficient in repeated-summer and chronological-summer wind tests; not universal | Bracket 48 h only in a regime where the winter/strong-wind comparison fails or shows age dependence |
| 200 m versus 100 m | 200 m engineering foundation exists; 100 m has geometry evidence only | One matched representative 100/200 m skill-and-cost A/B after the wind method survives contrasting regimes |
| Surface/PBL physics | Warm/dry V29 evidence and terrain/land candidates exist, but no wind-relevant causal benefit is shown | Intervene only after a regime comparison identifies a specific wind/PBL deficit |
| Observational added value | Not established | Compare retained HICAR and REA-L with identical observation times, masks, and sampling operators |

## Highest-information next experiment (not executed)

When experimental work resumes, the next comparison should be a stable-winter
Alpine-bridge replication. Reuse the corrected source, existing January REA-L
land/static initializations, and the summer comparison operator. The smallest
useful design is a restart-continuous reference, a same-start control to
measure numerical background variability, and one independently initialized
72-hour window whose final 24 hours cover the stable-winter target. Add a
second reset origin only if seam behavior remains part of the decision.

Scientific decision rule:

- If the reset winter cores also meet wind/PBL criteria, retain the 72 h / 48 h
  warm-up strategy as the leading wind method and test observational added
  value before more method engineering.
- If winter wind fails while errors decay with model age, extend the warm-up
  bracket only for that regime.
- If winter wind fails without decay or tracks reset surface-state errors,
  reject daily resetting for stable regimes and test restart-continuous
  integration or a specific land-state initialization/cycling intervention.

Do not launch a national, month, annual, 20-year, or 100 m campaign merely
because this bridge experiment finishes.

## Deferred production work

Defer production promotion gates, immutable runtime releases, exhaustive
manifests, archive/restore contracts, generalized annual orchestration,
checksum closure, production validators, cleanup campaigns, and throughput
scaling. Preserve the validated implementations, but repair or generalize them
only when they repeatedly block high-value experiments, threaten scientific
interpretation, or become part of the selected strategy.

## Evidence locators

- Corrected-wind qualification:
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/wind-tendency-fix-b514/v1/manifest.json`
- Corrected repeated-day equilibration:
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/repeated-day-summer-windfix-b514/v2/manifest.json`
- Chronological summer comparison:
  `/store_new/mch/msopr/olifu/icon_downscaling/qualification/chronological-overlap-summer-202007/v3/manifest.json`
- Historical detail and superseded decisions: `memory/project-state.md`

The legacy ledger is evidence, not a work queue. Results produced before the
horizontal-wind fix may inform infrastructure debugging but must not decide
the wind strategy.
