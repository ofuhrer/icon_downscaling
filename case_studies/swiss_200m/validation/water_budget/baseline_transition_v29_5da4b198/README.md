# V29 scientific-baseline transition

This directory freezes the attempt to treat HICAR commit
`5da4b1980497f20468e6e4b5b4c4a584849c3454` as a scientifically new baseline
rather than claiming that its cumulative water diagnostics are
trajectory-neutral.

The bounded 72-hour summer event completed on Balfrin, and its model,
provenance, solver, conservation, restart-checkpoint, water-budget,
energy-budget, wind, pressure, and precipitation gates passed. The independent
temperature screens did not:

- SwissMetNet height-adjusted 2 m temperature RMSE was `5.120 K`, versus
  `1.435 K` for REA-L and a frozen HICAR maximum of `3.435 K`; HICAR bias was
  `+3.663 K`.
- TabsD temperature RMSE was `4.365 K`, versus `1.025 K` for REA-L and a
  frozen HICAR maximum of `3.025 K`; HICAR bias was about `+3.382 K`.

The warm signal is interior and broad, with the largest TabsD biases in the
1000--2000 m elevation bands. Concurrent dry, high-shortwave, and very-low
modeled station-precipitation signals merit a future bounded process
diagnosis, but this evidence alone does not identify a cause.

The original checkpoint wrapper job failed because its immutable runtime
snapshot omitted a Python leaf imported by the wrapper. The failed report was
preserved. A new checksum-frozen `runtime_checkpoint_repair/` replayed only
the validator against the existing outputs; all 24-, 48-, and 72-hour
checkpoints passed. No model rerun was needed.

The canonical compact result is
`summer_transition_outcome.json`. The complete assessment and detailed
validator reports remain under the checksum-recorded Balfrin paths in that
file.

Decision: `HOLD_AND_DIAGNOSE`. Winter, the hour-48 restart overlap, paired
transition assessment, month, annual, 20-year, and 100 m science are not
authorized. No month-long simulation should be launched from this candidate.
