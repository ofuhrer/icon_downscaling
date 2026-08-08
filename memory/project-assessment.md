# ICON-to-HICAR decision assessment

## Objective

Choose a scientifically defensible ICON REA-L-CH1 to HICAR setup for Alpine
wind downscaling, then resolve remaining choices with small controlled
experiments. This is R&D, not a production-qualification programme. Keep only
enough provenance to reproduce a useful result.

The immediate target is a 200 m Switzerland setup. A 100 m or 20-year campaign
should wait until the setup survives contrasting regimes and shows added value.

## Converged experimental setup

These choices are the common baseline for the next experiments:

| Component | Selected setup | Status |
| --- | --- | --- |
| Model | Fully coupled HICAR; wind is the primary score, not `wind_only` physics | Selected |
| HICAR source | `feature/icon_downscaling` at `5d5574959f5c62feb183d184ab6ef99d2adfce80` until the radiation restart fix is integrated | Working baseline |
| Horizontal grid | Switzerland at 200 m using the validated national static | Selected for R&D |
| Vertical grid | 80 levels, 12 km top, 15 m lowest layer, stretch 0.65 | Selected starting point |
| Terrain coordinate | SLEVE decay 2/6; split smoothing window 5 and 10 cycles | Selected starting point |
| Wind adjustment | Discrete-adjoint variational projection, `Sx=true`, 500 m near-surface smoothing, RK3 | Selected |
| Meteorological IC/LBC | Exact target-native `hicarprep` products with native mass/U/V staggering | Selected architecture |
| Soil cold start | Native-grid TERRA SMI remapped by vertical overlap and reconstructed with target NoahMP hydraulics | Selected by project decision |
| Static soil | Four depth-dependent target layers with `soiltexture_var='soil_type_layer'` and `nmp_opt_soil=2` | Selected; dominant-soil mode is a diagnostic fallback |
| Lateral boundary | Sparse time-bracketing target-native LBC, initial 10 km shoulder, W diagnosed by HICAR | Usable provisional setup; width and W policy remain open |
| Terrain radiation | Horizon-aware direct beam plus diffuse sky-view correction | Available for uninterrupted sensitivity runs; off in restartable campaigns |

Relative-saturation soil transfer remains available as a sensitivity. Direct
absolute `W_SO` is a historical diagnostic only. The selected SMI method is
not expected to reproduce a legacy absolute-water HICAR trajectory; its large
water offset in that comparison remains a useful uncertainty to quantify.

Window length, overlap, warm-up, and retained-core ownership are deliberately
not selected here. Existing overlapping 72-hour results test one policy; they
do not fix production policy.

## What is established

- The corrected HICAR wind tendency evolves native and fixed-height wind under
  nonstationary forcing and passes multi-GPU halo and restart checks.
- The 200 m national geometry and 80-level SLEVE 2/6 configuration have
  representative engineering evidence.
- `hicarprep` produces strict target-native atmospheric state, surface state,
  and sparse LBC products. Three adjacent Storm Sabine states passed balance
  and runtime checks. The two-hour bracket-turnover run completed at
  `5d557495`.
- SMI and relative-saturation summer land-response arms both ran stably for six
  hours. Relative saturation crossed its predeclared dry threshold in only 14
  top-layer cells by less than `2e-5 m3 m-3`; this is retained as sensitivity
  evidence, not an open policy decision now that SMI is selected.
- Independent summer windows reproduced the continuous-reference wind after a
  48-hour warm-up in one bridge case, while soil and surface temperature did
  not. That supports parallel wind experiments but not a universal window
  policy or a general coupled-state product.
- Terrain-radiation direct/diffuse component behavior passes synthetic flat,
  horizon-blocked, and sky-view tests. Its remaining blocker is restart
  continuity, not the topographic radiation calculation itself.

## Open decisions and closure experiments

| Decision | Current uncertainty | Smallest useful experiment | Closure rule |
| --- | --- | --- | --- |
| Terrain radiation promotion | A fresh NoahMP process still loses unidentified trajectory state | No active branch. Reopen only with a hidden-state checksum instrument or an upstream NoahMP restart solution | Keep off in restartable campaigns; direct+diffuse may be used in an uninterrupted bounded sensitivity |
| LBC shoulder and W | Current 10 km/diagnosed-W run is stable but has large local W extrema | One strong-flow A/B over shoulder width and supplied versus diagnosed W | Select the least intrusive stable policy with no boundary-error penetration into the score region |
| Window policy | Summer 72/48/24 works for wind only; contrasting regimes absent | Run a winter/stable and a strong-wind case with multiple model ages; reuse the preemptible controller | Choose window/overlap from error decay and cost; do not assume 72 hours |
| Added value | HICAR versus REA-L skill is not established | Score both against the same observations, masks, elevations, and timestamps | Require robust wind benefit before 100 m or long campaigns |
| 100 versus 200 m | 100 m adds cost and sharper terrain but no demonstrated skill | Matched 100/200 m case after the baseline survives contrasting regimes | Move to 100 m only for measurable added value |

Surface-temperature lapse adjustment, alternate PBL/surface physics, layered
soil controls, and V29-like warm/dry changes are secondary sensitivities. Do
not revive them without a diagnosed error they can causally address.

## Terrain-radiation restart diagnosis

The uninterrupted and first-segment histories are bitwise identical at the
08:00 checkpoint, and the checkpoint history/restart common fields are also
bitwise identical. Divergence begins inside the first evolved NoahMP call
after restart.

The source audit located a concrete interface asymmetry. `EnergyVarOutTransfer`
writes radiative temperature, emissivity, roughness, albedo, vegetated/bare
ground temperatures, 2 m states, stomatal resistances, canopy gaps, and
exchange coefficients back to `NoahmpIO`. HICAR stores these fields in its
restart, but `NoahmpHICARmain` declares the latter group output-only and does
not map checkpoint values back into a fresh `NoahmpIO`. `EnergyVarInTransfer`
then also fails to restore them into the newly allocated NoahMP energy state.
This is untidy but not the restart cause: a bounded candidate made those states
`INOUT`, restored the complete mapping and specific-humidity conversion, and
reproduced the original ten-variable restart failure without changing any gate
metric. A prior QSFC-only intervention was also a clean negative result.

The working conclusion is that additional module-private NoahMP state or call
ordering is missing. No speculative patch or active branch is retained.
Terrain radiation is closed as an optional uninterrupted sensitivity and stays
off in preemptible/restart-linked work. Reopen it only when its value justifies
an instrumented hidden-state audit or an upstream NoahMP restart implementation.

## Branch and worktree closure

| Line of work | Disposition |
| --- | --- |
| Coordinator `codex/reorient-scientific-rd` | Keep as the integration line until reviewed into `main` |
| Coordinator hicarprep/overlap/wind-fix topic branches | Integrated or patch-equivalent; local labels deleted, remote labels may be deleted after review |
| HICAR `feature/icon_downscaling` | Keep as the sole active HICAR integration line |
| HICAR production-wind and NoahMP-state topic labels | Ancestors or tree-identical experiments; local labels deleted |
| `origin/codex/restart-noahmp-state-v26` and `origin/codex/v29-summer-warm-bias` | Superseded/patch-equivalent; remote deletion is safe after review |
| `origin/codex/restart-at-start` | Do not merge into the selected setup; retain remotely only if time-zero restart is still desired as a separate feature |
| Terrain-radiation scratch worktrees | Removed; the passing component and failing restart decision remain in the durable v4 artifact |
| Retired solver-research branches | Keep retired; recovery bundles are sufficient |

Remote branches are not part of the local cleanup action. Their disposition is
an executive deletion decision after the integration branches are reviewed.

## Evidence policy

Keep the current synthesis here, concise case records under
`case_studies/swiss_200m/validation`, and checksum-bound durable manifests for
expensive results. Delete superseded ledgers, completed implementation plans,
duplicate qualification reports, scratch-only source audits, generated build
trees, and stale worktrees. A failed experiment is retained only when it
changes a future decision.

Canonical evidence:

- Wind correction: `/store_new/mch/msopr/olifu/icon_downscaling/qualification/wind-tendency-fix-b514/v1/manifest.json`
- Chronological summer overlap: `/store_new/mch/msopr/olifu/icon_downscaling/qualification/chronological-overlap-summer-202007/v3/manifest.json`
- SMI land response: `case_studies/swiss_200m/validation/hicarprep_land_response_6h_20200702_v1.json`
- Sparse LBC storm run: `case_studies/swiss_200m/validation/hicarprep_storm_sparse_lbc_20200210_v1.json`
- Terrain radiation: `/store_new/mch/msopr/olifu/icon_downscaling/qualification/terrain_radiation_model_gate_v4`

Results produced before the horizontal-wind fix can inform debugging but must
not decide wind strategy.

## Ranked next work

1. Close the sparse-LBC shoulder/W choice with one strong-flow A/B.
2. Run contrasting-regime window-policy experiments with SMI cold starts in
   parallel on the preemptible queue.
3. Score HICAR and REA-L against identical wind observations.
4. Make the 100/200 m and long-campaign decisions from those results.

Production packaging, immutable releases, generalized annual orchestration,
exhaustive manifests, and archive ceremony remain deferred until they change
one of these decisions.
