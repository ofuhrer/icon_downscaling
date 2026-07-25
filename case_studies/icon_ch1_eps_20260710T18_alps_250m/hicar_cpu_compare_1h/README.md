# HICAR CPU Build Comparison, 1 h

This case compares the current Balfrin debug executable against a separate
CPU-only optimized release build using the frozen 250 m Alps input set.

The test keeps the physics and advection settings from the ready-file 33 h
case, but runs only from `2026-07-10 18:00:00` to `2026-07-10 19:00:00`.
The forcing list intentionally contains the full prepared forcing sequence so
that HICAR can read beyond the nominal end time if needed.

Run order on Balfrin:

```bash
cd $SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_cpu_compare_1h
sbatch scripts/build_hicar_release_cpu.sbatch
sbatch scripts/run_debug_1h.sbatch
sbatch --dependency=afterok:<build-job-id> scripts/run_release_1h.sbatch
```

After both runs complete:

```bash
python3 analysis/compare_hicar_outputs.py \
  --debug "debug_output/*.nc" \
  --release "release_output/*.nc" \
  --out analysis/hicar_cpu_compare_1h_report.txt
```

The comparison script is a regression guard, not a full production validation.
It checks output shape, finite values, time-coordinate agreement, and
variable-specific numerical drift.
