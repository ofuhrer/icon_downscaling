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
  2 m relative humidity, interval precipitation, 10 m wind speed, and wind
  vector RMSE. Temperature, humidity, and precipitation summaries must also
  provide HICAR/REA-L bias, model mean, and observation mean.
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
by elevation and terrain class.

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
