# HICAR 20 m readiness report

This directory is the reproducible, local reporting surface for the national
four-season HICAR assessment.  It is intentionally downstream-only: it does
not submit jobs, inspect Balfrin, or infer a scientific conclusion from missing
results.

## Required inputs

Copy the reviewed result files into `results/` and copy
`inputs.example.json` to `inputs.json`.  Every path inside `inputs.json` must be
relative to this directory; absolute paths and `..` traversal are rejected so
the eventual portable report retains safe, meaningful provenance.

The required inputs are:

- `geometry_validation.json`: output of `scripts/validate_sleve_geometry.py`.
- `campaign_evidence.json`: compact provenance and completion evidence for
  new hicarprep, the national pilot, and all four seasonal runs. The focused
  test fixture shows the fields the report reads.
- `restart_comparison.json`: exact restart-state comparison produced by
  `compare_restart_states_exact.py`.
- `national_summary.json` and `station_season_metrics.csv`: outputs from
  `scripts/national_campaign_postprocess.py`. They must cover 2 m temperature,
  2 m relative humidity, elevation-adjusted surface pressure, interval
  precipitation, 10 m wind speed, and wind-vector RMSE. Temperature, humidity,
  pressure, and precipitation summaries must also provide HICAR/REA-L bias,
  model mean, and observation mean.
- one footprint diagnostic JSON for each climatological season, produced by
  `scripts/diagnose_station_wind_footprints.py`.
- `reviewed_assessment.json`: the analyst-authored conclusion and prose,
  generated after inspecting the results. This is not an external approval
  state; it simply keeps scientific judgment out of generic rendering code.
  The builder assembles the evidence, but does not invent readiness claims.

The small fixture in `tests/test_hicar_readiness_report.py` is an executable
example. The builder reports all absent files together and checks only
conditions that affect interpretation: complete seasons and metrics, matching
row counts and times, adequate chart samples, and paired footprint counts.
The report never treats all station keys as valid for every metric: every row
shows its actual station or observation count, and wind results include counts
by elevation and terrain class. Seasonal headline results use every eligible
station available in that season; each metric's exact four-season eligible
station intersection is retained as a population-sensitivity table. For each
24-hour event the report requires evaluation-relative leads 1--24 for all six
ending-hour headline metrics. Physical simulation lead is retained separately;
evaluation lead 0 initializes the REA-L interval baseline and is not scored.
Wind-vector lead trajectories are also retained for terrain-ridge and
at-least-2000 m station strata so the 11--14 h transition around the restart
can be inspected directly. The few stations at or above 3000 m remain named
station evidence rather than a network aggregate. Lead 24 is shown but
excluded from descriptive fitted slopes because the staged instantaneous
REA-L fields switch native cycle there.
Seasonal tables use equal-station network RMSE
(`sqrt(mean(station RMSE squared))`) as the primary score. They separately
retain the arithmetic mean of station RMSEs and the observation-pair-pooled
RMSE reconstructed with station pair counts; these are distinct estimands and
are labelled accordingly.

## Reproduce the analysis

From the repository root:

```bash
python analysis/hicar_readiness_20m/build_artifact.py \
  --inputs analysis/hicar_readiness_20m/inputs.json \
  --output analysis/hicar_readiness_20m/artifact.json

HICAR_READINESS_INPUTS=analysis/hicar_readiness_20m/inputs.json \
python -m jupyter nbconvert --execute --to notebook --inplace \
  analysis/hicar_readiness_20m/readiness_analysis.ipynb
```

Both commands are expected to fail until every real result is present.  Once
the evidence is complete, the two outputs use the same loader and deterministic
derived datasets.  The notebook is the audit-friendly companion analysis;
`artifact.json` is the canonical technical-report input.

Do not hand-author `report.html`.  After the scientific assessment has been
reviewed, package the canonical artifact once with the Data Analytics portable
report builder described by the report workflow.
