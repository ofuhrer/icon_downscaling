# HICAR OpenACC A100 Build Attempt

This case is a GPU/OpenACC follow-up to the CPU debug-vs-release comparison. It
uses the same frozen 250 m Alps inputs and the same one-hour model window:
`2026-07-10 18:00:00` to `2026-07-10 19:00:00`.

Balfrin GPU-node notes gathered for this attempt:

- Confluence `Building ICON on Balfrin` recommends ICON's
  `config/cscs/alps_mch.gpu.nvidia_mixed` wrapper for the operational GPU setup.
- Confluence `Setting up ICON on Balfrin` uses the `debug` partition for short
  GPU tests and requests GPUs with `--gres=gpu:4`.
- Live Slurm inspection showed GPU nodes with 128 CPUs, about 446 GiB host
  memory, `gpu:4`, and AMD EPYC 7713/7763 CPUs.
- The target GPUs for this HICAR attempt are Balfrin's 4 NVIDIA A100 96 GB GPUs,
  so the build uses NVHPC target `cc80`.

Build:

```bash
cd $SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m/hicar_openacc_1h
sbatch scripts/build_hicar_openacc_a100.sbatch
```

If the build creates `$SCRATCH/icon_hicar/HICAR/bin/HICAR_gpu`, run:

```bash
sbatch scripts/run_openacc_1h_debug.sbatch
```

The OpenACC code path in `src/physics/linear_winds.F90` calls cuFFT directly for
GPU inverse FFTs. The build script still provides regular FFTW headers/libs for
the non-OpenACC FFTW interface and explicitly checks that `HICAR_gpu` links
against `libcufft`.
