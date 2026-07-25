# ICON-to-HICAR project state

## Objective and source control

Build a reproducible workflow for dynamically downscaling MeteoSwiss ICON
output from roughly 1 km to 100--250 m over Alpine domains up to Switzerland
plus a boundary margin.

- HICAR source: `/Users/fuhrer/Work/agentic/icon_hicar/HICAR`
- Remote: `git@github.com:ofuhrer/HICAR.git`
- Production-performance branch: `feature/icon_downscaling`, pushed through
  `d6c52a54` on `origin/feature/icon_downscaling`.
- Active solver-research branch: `feature/wind-galerkin-multilevel`, pushed
  through `16bdb27b`. This is an opt-in validated national candidate; the
  production-performance baseline remains `feature/icon_downscaling`.
  Earlier retired multigrid and micro-optimization work is preserved in
  `archives/hicar-consolidation-20260721.bundle`; its final safe tip was
  `e050ed5e`, with coarse corrections disabled.
- The isolated Swiss initialization-validation source, including its strict
  exact-operator bootstrap, is archived separately in
  `archives/hicar-swiss-init-validation-20260721.bundle` and the recoverable
  `archives/HICAR-swiss-init-validation-retired-20260721/` clone.
- Fieldextra is a source reference. Use the verified operational executable;
  do not compile it for normal workflow runs.

## Validated workflow baseline

- ICON native triangles are regridded with operational fieldextra to a
  structured HICAR forcing grid. The package retains full-level `P,QV,T,U,V`,
  half-level `W`, `HFL/HHL`, `HSURF`, `FR_LAND`, and bottom-to-top vertical
  order.
- ICON REA-L-CH1 is readable from the `rea-l-ch1` FDB view. It is hourly,
  cycle-plus-step data. Geometry is step 0; dynamic fields and `W` use the
  requested step.
- REA-L surface wind diagnostics are also available at ten-minute steps:
  instantaneous `U_10M`/`V_10M` are parameters `500027`/`500029`, and
  `VMAX_10M` parameter `500164` is the maximum over the preceding ten
  minutes. The `step=1` gust record covers only minutes 50--60, so an hourly
  maximum requires the six ten-minute records. Treat VMAX as a scalar
  diagnostic sidecar, not as HICAR dynamical forcing.
- Public static construction uses Copernicus DEM GLO-30, ESA WorldCover 2021
  (USGS mapping), SoilGrids texture, and ICON `HSURF` blending at boundaries.
- The frozen 250 m Alpine regression case has completed CPU and one-hour
  ICON-derived validations. Use it for workflow regression, not as a generic
  domain template.
- The four-GPU NCCL layout is four GPU compute ranks plus one CPU-only I/O
  rank per node, with MPICH GPU support disabled uniformly. GPU-aware MPI
  without NCCL is faster on one node; NCCL is the validated multi-node mode.
- Production candidate configuration is 80 levels to 12 km, variational
  winds, `Sx`, SLEVE 2/6, HICAR terrain split window 5 / 10 cycles, and
  500 m wind smoothing. The 60-level fallback is rejected for the current
  Swiss case.

## Integrated HICAR improvements

`feature/icon_downscaling` includes the validated OpenACC halo fix, count-safe
first-output I/O, Morrison sedimentation tuning and 32-lane vectorization,
Morrison diagnostic removal, PBL scalar-coefficient factoring, fused
third-order uncorrected advection divergence, count-safe slab I/O, and RRTMGP
batch partitioning. These were output/timer-validated on representative GPU
cases. Retired experiment branches and worktrees were removed after verified
Git-bundle archival.

The legacy-`mpi` production branch was rebuilt locally with Open MPI/GFortran
and passed the four-rank iterative-wind and full halo suites. Balfrin
revalidation of `443f60f1` succeeded on NVHPC 24.5, Cray MPICH 8.1.30, CUDA
12.3, and NCCL: the four-rank GPU halo suite passed, and the two-node layout
(eight GPU compute plus two CPU I/O ranks) completed the one-hour 250 m
Switzerland smoke with NetCDF output. Ready-file waits now print the data and
marker paths, timeout, and poll interval to stdout (`8bb75034`); verbose
iterative-wind solves also print a flushed start record and a residual/
elapsed-time heartbeat every 100 iterations (`2ef5354c`).
The vertical-line block-Jacobi preconditioner (`07bb8451`) factors the exact
vertical tridiagonal block in every terrain-following column. It passes local
four-rank iterative-wind and halo suites and the Balfrin NVHPC/Cray-MPICH
two-node 250 m regression: initial/probed/timestep solves converged in
58/96/158 iterations, the one-hour output completed, and multi-node NCCL was
active. The same target binary also passed the production four-GPU NCCL halo
suite and a second two-node multi-node smoke run with NetCDF output. The
four-node Swiss acceptance run (`4908222`) failed decisively at the calibrated
post-probe solve: native FGMRES stalled at `5.17e-1` after 2500 iterations
against a `5.10e-4` target, and the legacy BiCGStab retry stopped at
`2.64e-2`.  Its initial physical solve and operator-equivalence gate passed;
the allocation was cancelled. Do not treat the Swiss wind gate as passed.
`400fbe95` makes a non-zero Krylov status after all preconditioner retries
fatal; a partial residual decrease is not convergence and must not be used to
update physical winds.
The short-lived `24b4c4a3` probe-halo experiment was output-identical on the
four-node Swiss run; `333d6d07` removes it because the current `w_to_grid`
correction kernel does not read lambda ghosts. Local four-rank iterative-wind
and full halo tests pass; target-stack revalidation also passed: clean NVHPC 24.5 / Cray MPICH 8.1.30 /
CUDA 12.3 / NCCL build job `4900435`, four-GPU NCCL halo job `4900566`, and
two-node (8 compute GPU + 2 I/O) one-hour 250 m smoke job `4900656`. The latter
converged in 58/96/158 iterations for its initial/probed/timestep solves and
completed NetCDF output. Four-node Swiss gate `4900660` reached a converged
three-sweep initial solve (`2.5485e-4 / 57.7411`) but then stopped, as
intended by `400fbe95`, on a non-converged post-probe solve (`2.6428e-2 /
51.0236`, status 1); the allocation was cancelled. The national gate remains
unpassed.
`7e85c44d` adds an independent calibration check on a deterministic
non-coloured distributed vector: it compares the reconstructed matrix with a
direct `2*D(G(lambda))` application and reports global relative L2, global
maximum, and rank-interface maximum errors. A clean NVHPC/Cray-MPICH rebuild
(`4900877`) and the two-node 250 m NCCL regression (`4900923`) passed; the
operator comparison was `6.9653e-8` relative L2, `2.6537e-9` maximum, and
`1.8560e-9` at rank interfaces. Thus matrix reconstruction is validated for
that multi-node case. Four-node Swiss diagnostic job `4900926` reached the
same conclusion on the national decomposition: `6.5149e-8` relative L2,
`1.0778e-6` maximum, and only `3.0011e-9` at rank interfaces. It therefore
rules out a distributed probe/matrix reconstruction defect. The physical
bootstrap still required the three-sweep retry (`2.5485e-4 / 57.7411`), while
the post-probe solve diverged at two sweeps (`1.2724e36 / 51.0236`) and then
stalled at three (`2.6428e-2 / 51.0236`, status 1). The fatal guard stopped
the compute ranks and the released allocation was cancelled. The remaining
work is a genuinely stronger scalable preconditioner or solver, not another
operator or namelist experiment.
`3a80f01f` adds the mandatory independent final residual check:
after a recurrence reports convergence, HICAR recomputes `||b-Ax||` and global
`max |x|` from the final solution and rejects false convergence before applying
any wind correction. The restored validated two-sweep baseline passed the
four-rank local iterative-wind test with this guard. Balfrin revalidation of
the same commit passed the current four-GPU NCCL halo suite (`4901116`) and
the two-node one-hour 250 m NCCL regression (`4901117`), which completed
NetCDF output after 58/96/158-iteration initial/probed/timestep solves.
The current `7e85c44d` target-stack halo regression also passed on four GPUs
with NCCL (`4900927`): batch, scalar, 2-D, U-staggered, and V-staggered halo
exchange tests all completed successfully.
`45d4f486` adds reproducible Balfrin build and device-runtime checks for the
pinned HYPRE v2.32.0 managed dependency. It built successfully with NVHPC
24.5, CUDA 12.3, and GPU-aware Cray MPICH 8.1.30 (`4901222`); HYPRE's
maintained CUDA-native four-rank IJ/ParCSR test then completed on four GPUs
(`4901236`). BoomerAMG converged in 24 cycles to relative residual
`8.135371e-09` (2.121 s solve after 9.286 s setup). This validates the
external backend and its device/MPI transport, but HICAR has not yet assembled
its exact wind operator into HYPRE or selected it in production.
The subsequent exact-operator HYPRE integration is validated on the same
target stack: a decomposition-aware ParCSR assembly, BoomerAMG-preconditioned
FGMRES, an independent native true-residual guard, and a one-time
HYPRE-versus-native matrix-action gate. The four-rank adapter smoke completed
in 7 iterations at relative residual `7.46e-9` (`4902118`). The 250 m Alpine
regression completed with halo/output/timer gates on one node (`4902073`) and
two NCCL nodes (`4902119`); its operator agreement was respectively
`5.85e-15` and `5.78e-15`, with no rejected or failed HYPRE solve. The HYPRE
path currently uses host HYPRE memory/execution because the validated NCCL
topology intentionally disables GPU-aware MPICH for its CPU-only I/O rank.
`13073f3b` changes the external AMG policy to HMIS coarsening, extended+i
interpolation, bounded interpolation density, and cached FGMRES/AMG setup for
the immutable calibrated matrix. It passed the full one-node 250 m regression
(`4904188`): exactly one `0.963 s` setup, 25 accepted 6--7 iteration solves,
no residual rejection/failure, timing summary, and NetCDF output. The prior
default-Falgout national baseline (`4903101`) was cancelled after 48m45s in
first setup with stable `~53 GB/rank` memory and no iteration; do not use that
default policy for Swiss scale.
Swiss diagnostics now rule out an HYPRE/native operator mismatch: the exact
matrix-action gate is `6.5149e-8` relative L2 on the 16-rank decomposition and
the independent native true-residual guard remains active.  Plain one-cycle
HMIS AMG setup completes in `447 s`, but FGMRES stagnates at relative residual
`2.8568e-2`; two AMG cycles are worse (`~2.12e-1` after 136 iterations).
Both changes passed adapter and two-node 250 m regression but failed the
Swiss acceptance trace, so their commits (`eb6bd8c5`, `06821b85`) were
explicitly reverted by `b2224428`.  The RAS-ILU(0)-smoothed HMIS variant did
not complete Swiss setup after over ten minutes at a stable `~39 GB/rank` and
was cancelled.  The production branch is therefore restored to the one-cycle
HMIS baseline while a stronger scalable coarse-space/preconditioner design is
still required; do not repeat these variants as a namelist or parameter sweep.
`f335b098` replaces the plain AMG preconditioner with HYPRE MGR's explicit
distributed 2x2x2 spatial C/F coarse space and an HMIS AMG coarse solve.  It
compiled on the target stack (`4907495`), passed the four-rank adapter with a
`7.46e-9` relative residual in seven iterations (`4907545`), and completed the
two-node 250 m halo/output regression (`4907546`): setup 0.585 s, all 24 hourly
solves converged in seven iterations with accepted independent true residuals.
It failed the four-node Swiss gate (`4907563`): after the correct native
calibration (`6.5149e-8` relative L2), MGR setup produced no Krylov iteration
over more than 447 s while resident memory grew to about 36 GB/rank.  The run
was cancelled at 11m36s; `69ae801d` and `e09d7c10` revert its logging and
implementation from the production branch.  Do not repeat this MGR split
without a separate setup-cost design and small-scale validation.
`57976bbd` replaces the rejected AMG/MGR hierarchy with FGMRES using HICAR's
exact vertical-line preconditioner.  The target-stack adapter passed in 22
iterations at `9.77e-9` relative residual (`4907857`) with zero setup time;
the two-node 250 m regression (`4907858`) completed all 25 solves, with
accepted independent residuals in 123--235 iterations and successful output.
`ff49edbc` gives this backend an accurate stdout marker.  It failed the
four-node Swiss gate (`4907879`): matrix setup completed in 0.006 s but the
first Krylov solve produced no result within the bounded window and held about
47 GB/rank.  The allocation was cancelled at 9m55s.  `ac588ab5` and
`0672b185` revert the logging and implementation; do not repeat this path
without first resolving its Swiss Krylov memory/time behaviour.
The safe baseline was rebuilt cleanly on the target stack (`4908018`) and its
four-rank adapter recheck passed (`4908022`: seven iterations,
`7.46e-9` relative residual).  A controlled verbose adapter run (`4908045`)
confirmed that HYPRE emits an iteration-by-iteration residual table once it
enters FGMRES.  In the rejected Swiss line-preconditioned run, stdout stopped
immediately after HYPRE's initial residual and resident memory subsequently
reached about 50 GB/rank.  The unresolved cost is therefore host FGMRES
Krylov-workspace initialization before its first iteration, not matrix setup,
AMG setup, or missing logging.  The next implementation should avoid that
host Krylov allocation (for example a bounded, GPU-resident restarted native
GMRES using the existing exact vertical-line preconditioner); do not submit a
further Swiss HYPRE variant before a small-scale implementation validation.
`f45427fc` implements that replacement: a bounded restart-50, GPU-resident
right-preconditioned FGMRES using the existing exact vertical-line
preconditioner, plus an independent final residual check normalized by
`||b||`.  It was built cleanly on Balfrin NVHPC 24.5 / Cray MPICH 8.1.30 /
CUDA 12.3 / NCCL (`4908165`) and passed the four-GPU halo suite and a two-node
24-hour 250 m regression (`4908169`): 20 halo checks passed, all 25 FGMRES
solves passed the independent true-residual gate (189--345 iterations), and
two NetCDF outputs were produced.  Peak task RSS was about 1.8 GB, not the
host-FGMRES tens-of-GB growth.  `a753c78a` updates the Swiss gate to require
native operator-equivalence and FGMRES acceptance markers. Four-node Swiss
job `4908222` verified the operator (`relative L2=6.51e-8`, maximum absolute
error `1.08e-6`, rank-interface error `3.00e-9`) but exposed post-probe
preconditioner insufficiency; national acceptance remains unpassed until a
solver change completes all residual, output, and physical gates.
`d6c52a54` addresses the observed restarted-Krylov stagnation without reviving
the rejected host-HYPRE path: it increases the bounded GPU restart window from
50 to 100 vectors and adds a second Arnoldi orthogonalization pass. Target
stack build `4908524` completed cleanly. The required two-node 250 m
halo/output regression `4908616` passed in 1m48s: all 20 four-GPU NCCL halo
checks passed, the projection-operator comparison remained at `6.9653e-8`
relative L2, all 25 native FGMRES solves passed the independent true-residual
gate in 155--184 iterations, the timing gate was present, and the batch script's
NetCDF-output check succeeded. This qualifies the change for another Swiss
submission. Four-node Swiss revalidation `4908688` failed after the calibrated
operator gate: restart-100/reorthogonalized FGMRES improved the 2500-iteration
residual from the restart-50 result (`5.17e-1`) to `1.12e-1`, but remained far
above the `5.10e-4` target and was rejected. Its step reported `54804976K`
maximum RSS, showing that the larger GPU basis also retains a material host
allocation. The allocation was cancelled at 7m11s; national acceptance is
still unpassed.
`6568c395` repairs the target-stack `wind_iterative` unit fixture by entering
its locally allocated forcing-tendency `u/v` arrays into OpenACC data before
the correction kernel.  The four-rank NCCL unit suite passed on every rank
(`4908312`).  It covers the analytic solver and the full correction/halo path;
the calibrated native-FGMRES path remains covered by the two-node 250 m
integration regression above.

## Current national-scale gate: Switzerland 200 m

- The static domain is 2271 x 1651 (3,749,421 cells), preserving the 454 x
  330 km footprint, at least 50 km external margin, a 30 km REA-L terrain
  blend, and at least 20.216 km unblended Swiss buffer. Its SHA-256 is
  `bf03fa8ce45270bb1bfb4e7f987b2af59e0a18db7a16b1d83fd2ed33c64b7372`.
- Do not describe the 81 x 81 Alpine 250 m case as representative of Swiss
  solver convergence. It is a correctness/performance regression only: the
  Swiss grid has about 571 times as many horizontal cells and spans roughly
  454 x 330 km instead of 20 x 20 km. On the relaxed `topo` fields, the
  elevation maximum is 4,740 m versus 2,635 m and the largest one-cell height
  increment divided by grid spacing is about 2.24 versus 0.88. A regional
  bridge case and the bounded Swiss diagnostic are required for spectral and
  convergence conclusions.
- The former window-5/100-cycle SLEVE terrain split had a misleading positive
  analytic `gamma=0.1253` but an actually inverted national grid: minimum
  mass Jacobian `-0.0965` and minimum adjacent mass-level spacing `-8.34 m`.
  Use the unchanged static DEM with the validated window-5/10-cycle split.
  It gives actual minimum mass Jacobian `0.17194` and minimum interface
  thickness `12.257 m`. The runtime now globally gates the constructed mass
  Jacobian and interface thickness before wind initialization.
- Three schema-validated REA-L hourly forcing files (00, 01, 02 UTC) and the
  forcing list are published under `case_studies/swiss_200m/forcing/rea_l_ch1`.
  The Fortran list-directed forcing manifest requires each pathname to be
  double-quoted; an unquoted manifest can yield a corrupt filename and spend
  the full ready-file timeout waiting for a non-existent marker.
- The minimum viable layout is four nodes: 16 compute GPUs plus four CPU I/O
  ranks in a 4x4 compute decomposition. The two-node 4x2 layout overflows a
  signed MPI halo-message count in later physics.
- The old non-adjoint projection failed the national gate and is retired.
  The discretely adjoint `K=B M^-1 B^T` path with the exact Galerkin
  hierarchy passes the national solver and conservation gates; do not infer
  current status from the retained legacy failure history below.
- Operator-audit branch `feature/wind-operator-audit` at `7db4ba8a` adds
  opt-in structural and first-cycle Arnoldi diagnostics without weakening any
  production acceptance gate. NVHPC/NCCL build `4908938` passed. The 81 x 81
  250 m regression audit `4908904` passed its 24-hour model/output gate. The
  bounded Swiss audit `4908943` preserved the full analytic-bootstrap solve,
  passed operator equivalence (`6.5149e-8` relative L2), captured one
  100-vector calibrated cycle (`51.024 -> 0.51487`, target `5.1024e-4`), and
  terminated before physics as designed; peak step RSS was about 28.9 GB.
- On the Swiss physical right-hand sides, the Jacobian-weighted composed
  operator has negative Rayleigh quotients and only `1.02e-4`--`1.45e-4`
  sampled adjoint defect. The right-preconditioned Arnoldi operator is instead
  extremely nonnormal: projected field-of-values bounds about
  `[-75.67, 75.49]`, Henrici departure `0.993`, and normalized commutator
  `0.881`. The calibrated failing RHS projects about 1.8 times more strongly
  than the bootstrap RHS onto the extracted harmonic-Ritz directions. This
  moves bounded recycling behind terrain conditioning and a
  Jacobian-consistent tensor-product/horizontal multilevel preconditioner in
  production priority; recycling remains a diagnostic/accelerator, not the
  primary cure.
- External terrain conditioning is implemented in
  `scripts/filter_static_topography.py` and
  `scripts/hicar_static_topography.py`; HICAR runtime topography remains
  untouched. The tool copies the complete static file, changes only terrain
  fields, preserves land/water and all non-terrain variables, filters the
  high-resolution terrain before reapplying the existing REA-L boundary
  blend, validates, atomically publishes, and records checksums and
  multi-scale change metrics. Use the order-8 Shapiro sensitivity rather
  than a repeated second-order 1-2-1 smoother: one pass has nominal response
  0/0.684/0.938/0.986 at 2/3/4/5 grid-cell wavelengths and essentially unit
  response beyond 10 cells. On Swiss terrain its 5 km and 10 km block-mean
  RMS changes are 0.045 m and 0.017 m; the mean land-height change is 0.004 m.
  Published sensitivities and manifests are under
  `case_studies/swiss_200m/static_sensitivities/`. Keep water elevations
  unchanged by default because the USGS water class includes Alpine lakes.
- Swiss sensitivity jobs `4909112`/`4909113` exposed a bootstrap retry bug:
  a rejected solve that reduced its residual by more than 100x did not try
  the available stronger line-preconditioner sweep count. They were cancelled
  after the compute ranks stopped and I/O ranks waited. Commit `15dfdb34`
  retries every non-converged bootstrap solve while stronger sweeps remain;
  target NVHPC/NCCL build `4909133` passed. Replacement audits `4909135` and
  `4909136` completed for the order-8 one- and two-pass sensitivities. Both
  passed operator equivalence and reached the calibrated audit, but terrain
  filtering made normalized first-cycle convergence worse: unfiltered
  `0.5149/51.02 = 1.01e-2`, one-pass `0.5893/20.46 = 2.88e-2`, and two-pass
  `0.5409/20.62 = 2.62e-2`. Counts of harmonic Ritz values below
  0.01/0.05/0.1/0.5/1 remained 2/9/14/38/56 (unfiltered
  2/9/14/36/56), while projected nonnormality increased from 0.881 to
  1.048/1.299 and field-of-values bounds widened from about
  `[-75.7,75.5]` to `[-119.6,98.6]`/`[-446.0,559.2]`. Do not intensify DEM
  filtering as a solver intervention; retain it only as a separately justified
  physical/numerical sensitivity. The global coarse correction is again the
  primary solver path.
- Commit `9ef6dcc2` adds bounded 16x12x8 spatial sketches of each first-cycle
  Arnoldi basis vector and reconstructs centroids, spreads, dominant regions,
  and coarse-scale energy for slow harmonic Ritz modes offline. Python unit
  tests pass, the changed Fortran source compiles locally (the full local
  target still stops later at the pre-existing `output_interface.smod`
  failure), and target NVHPC/NCCL build `4909188` passed. Unfiltered spatial
  audit `4909234` completed with unchanged operator equivalence and calibrated
  residual. The slow modes have only about `1e-6` or less of their represented
  energy in signed 28x27 km / 8-level bin means, with broad horizontal and
  vertical spreads. They are therefore not simple smooth domain-wide waves;
  use metric-aware interpolation/smoothing rather than assuming that a few
  geometric low-frequency modes are an adequate coarse space.
- Direct D/G audit job `4909256` is invalid and must not be used: the first
  implementation overwrote live tendency workspaces, reported zero gradient
  forms, and contaminated the following calibrated right-hand side. Commit
  `e2fc4b86` reverts it, and clean target rebuild `4909304` completed in
  3m52s. A second scratch-field attempt also failed target runtime validation
  (`4909445`): NVHPC optional-array/OpenACC handling yielded zero gradient
  forms and corrupted the next physical RHS despite no intentional tendency
  writes. Commits `b8d9b1ca`, `91011430`, and `7db4ba8a` revert that attempt.
  Do not use any D/G numbers or convergence markers from `4909445`. A future
  direct D/G test must use a genuinely pure explicit-array kernel or a
  standalone harness, never optional arrays through the production GPU
  divergence routine. Clean target rebuild `4909505` from reverted commit
  `7db4ba8a` completed in 3m23s, restoring the validated executable baseline.
- The archived native multigrid failures do not test the currently recommended
  tensor-product method. Their coarse operator was a point-sampled,
  rediscretized approximation, intermediate smoothers were damped point
  Jacobi, and the attempted vertical-line smoother was reverted; terminal
  agglomeration could not repair that inconsistent hierarchy. Reuse only its
  distributed layout/halo machinery. A new production hierarchy must use
  Jacobian-adjoint horizontal transfers, `A_c = R A_f P`, exact vertical line
  relaxation on every level, and staged communicator/rank agglomeration.
- Production multilevel work is active on HICAR branch
  `feature/wind-galerkin-multilevel` at `93118b0e`. The new isolated modules
  provide boundary-aware bilinear horizontal interpolation, normalized
  metric-weighted adjoint restriction, globally aligned/unique coarse-tile
  ownership, exact nonsymmetric `R A P` stencil recovery by 27-color probing,
  exact coarse vertical-block factorization and line relaxation, and a
  two-stage MPI halo exchange that propagates the corner cells required by
  the resulting 3x3 horizontal stencil. The reference global and decomposed
  tile paths agree for odd/even and offset extents and preserve identity
  boundaries. A feature-gated (`HICAR_WIND_MULTILEVEL=1`) distributed
  one-level Petrov-Galerkin V-cycle is now wired into the native wind
  preconditioner. It uses the exact vertical lines on the fine and coarse
  grids, exact colored `A_c = R A_f P` assembly, and permanent host-stencil,
  device-halo, and device-stencil equivalence checks. Clean four-GPU Balfrin
  gate `4910695` completed successfully: all five transfer/device/offset/halo/wind
  tests passed, device halo error was zero, and host and device coarse actions
  matched direct `R A P` to `2.2909e-16`. NVHPC device kernels must receive
  allocatable halo, stencil, and vertical-factor storage as explicit
  plain-array arguments; accessing those components through a polymorphic
  object either left sends zero or caused rank-local kernel stalls on the
  target compiler. Commits `907d8fad` and `4e1fcc66` extend that invariant to
  the coarse stencil and vertical line solve. The two-node 24-hour 250 m
  integration gate `4910696` then completed in 1m28s with operator equivalence
  `6.9653e-8`, exact coarse verification `3.0682e-16`, all 25 independent true
  residual gates accepted, 18--26 iterations (mean 22.84), about 1.79 GB peak
  task RSS, timing output, and two NetCDF files. The validated line-only
  restart-100 regression needed 155--184 iterations, so the coarse correction
  is materially effective on this small case. This milestone still validates
  only one distributed level and has no regional or Swiss convergence
  evidence. Commit `730dc09f` generalizes it to a recursive exact-Galerkin
  hierarchy with permanent equivalence checks at every level and a smoke gate
  that requires a V-cycle to reduce the residual. Four-GPU target job `4910820`
  built six exact coarse levels (84x73 to 4x4), with zero device-halo error and
  `R A P` errors of order `1e-16`. On the eight-rank two-node layout, commit
  `93118b0e` now stops before any rank would lose all coarse ownership; job
  `4910827` therefore built four levels (42x42, 22x22, 12x12, 7x7) and completed
  the 24-hour 250 m integration in 1m25s. All 25 independent true-residual
  gates passed in 5--6 iterations (mean 5.04), peak task RSS was about 1.79 GB,
  timing output and two NetCDF files were present, and every recursive coarse
  action matched direct `R A P` within `3.48e-16`. This is a large improvement
  over both the one-level 18--26 iterations and line-only 155--184 iterations,
  without increasing end-to-end regression time. It is still not
  production-ready.
- Commit `0ba5f9ac` moved exact-operator calibration and hierarchy setup ahead
  of the legacy bootstrap, so bounded audits can interrogate the actual raw
  national system; `73c92c79` bounds FGMRES at restart 20. The 701 x 701,
  140 km regional bridge is steeper than the Swiss domain by normalized
  one-cell increments (about 2.26/2.35 in x/y) and converges in six
  iterations. It proves steepness alone does not reproduce the national
  failure. Swiss job `4910974` passed operator and exact-RAP gates through
  nine levels (terminal 6 x 5 x 82) but reduced the raw residual only
  `57.526 -> 4.983` in 100 iterations. Geographic extent/global modes matter;
  the bridge is a necessary regression, not a representative convergence
  proxy.
- Commit `96f5a9c6` exposes bounded terminal line relaxation through
  `HICAR_WIND_COARSEST_SWEEPS` (default four). Swiss job `4911053` increased
  the terminal work to 32 sweeps and improved the 100-iteration result from
  `4.983` to `1.958`, still over 3,000 times the `5.7741e-4` target. An
  inexact terminal solve matters but is not the primary cure; the next
  multilevel structural step is a true coarse solve with staged rank
  agglomeration.
- Commits `071b7578`, `fc0ba1a5`, and `b68d066e` add an opt-in bounded
  four-vector recycle experiment to restart-20 FGMRES. Swiss audit `4911178`
  reached `1.098` after 100 iterations but failed its own `A U = C` algebraic
  gate (`0.967` defect), so that residual is diagnostic only. The first-cycle
  Hessenberg matrix spans more than ten orders of magnitude; reconstructing
  its smallest singular images falls below the calibrated operator's
  numerical precision. Commit `cbdc9dfe` instead forms each recycled image
  with the production operator. Corrected Swiss audit `4911246` passed the
  relation gate at `2.052e-11` but produced essentially the same residual,
  `57.526 -> 1.0983` after 100 iterations against a `5.7741e-4` target, with
  about 66 GB peak reported RSS. This smallest-singular-vector recycle is
  valid but ineffective and is not the lead path. A true terminal coarse
  solve with rank agglomeration is next; a harmonic-Ritz GCRO-DR experiment
  may remain a strictly bounded secondary test.
- Commits `657b3eb4`, `335528af`, `763fecf6`, `0ade95e4`, and `40a7c3df`
  replace terminal relaxation with a collective exact-Galerkin solve. After
  nine levels the Swiss terminal grid is only 6 x 5 x 82, or 960 interior
  unknowns; the implementation gathers the exact 27-point operator to one
  rank and applies a full two-pass-MGS GMRES solve with bounded iterative
  refinement. Local one- and four-rank bounds-checked tests and the complete
  four-rank HICAR suite pass. The hard 701 x 701 regional bridge job
  `4911473` passed the terminal known-solution gate and converged the physical
  solve in six iterations (`26.552 -> 1.2111e-4`, target `2.6458e-4`).
  Swiss audit `4911676` passed operator equivalence, all nine exact-RAP gates,
  the terminal known-solution gate (`1.7959e-12` residual), and the first
  physical terminal solve (`3.0357e-11` relative residual), but outer
  restart-20 FGMRES still ended at `3.4197` after 100 iterations from
  `57.5263` against a `5.7741e-4` target. Peak reported step RSS was about
  57 GB. Terminal inexactness is therefore ruled out as the primary cause.
- Commit `3dc17be9` implements the final bounded Krylov-side experiment:
  four-vector harmonic-Ritz GCRO-DR recycling, retaining real and imaginary
  members of a complex pair together and forming every recycled image with
  the production operator. Local and Balfrin harmonic eigensolver/unit gates
  pass. Swiss audit `4911849` entered the true harmonic path, retained
  magnitudes `1.004, 1.562, 1.562, 4.879`, and passed the `A U = C` relation
  at `2.700e-12`; it nevertheless ended at `2.0468` after 100 iterations from
  `57.5263`, over 3,500 times the `5.7741e-4` target. This is better than
  terminal-exact restart-20 without recycling (`3.4197`) but worse than the
  valid smallest-singular recycle (`1.0983`) and much worse than restart-100
  (`0.112`). Recycle selection, restart length, local smoothing, exact RAP,
  and terminal accuracy are now exhausted as production paths. The next
  solver work must reformulate the discrete variational projection so that
  divergence and correction gradient are adjoints in an explicit
  Jacobian/mass-weighted inner product, yielding an SPD or much closer to
  normal elliptic operator; then use a tensor-product/semicoarsened multigrid
  designed for that operator. Keep the validated exact hierarchy as a
  regression harness, not as proof that the current nonnormal operator is
  salvageable.
- Commit `ba1800e6` implements the first opt-in discretely adjoint production
  path (`HICAR_WIND_ADJOINT_PROJECTION=1`). It defines the conservative
  volume-integrated constraint `B`, the correction `-M^-1 B^T`, and the
  analytic seven-point SPD Schur complement `K=B M^-1 B^T`; the diagonal
  mass-flux energy exactly reproduces the legacy flat-grid/non-cross
  correction coefficients. Density cancels from the Schur conductances, and
  the correction now uses the same extrapolated boundary density as
  `calc_divergence`. The existing exact-Galerkin hierarchy is reused under
  `HICAR_WIND_MULTILEVEL=1`. Local four-rank tests pass with nonuniform
  density, map/Jacobian weights, distributed symmetry and positive-energy
  checks, exact RAP errors of `2--3e-16`, seven outer iterations, status zero,
  a `7.84e-6` independent matrix residual, and a `1.01e-6` independently
  recomputed physical mass-constraint residual after the configured
  two-pass mixed-precision refinement. The legacy four-rank suite also
  remains green. This is local CPU evidence only; target NVHPC/OpenACC/NCCL,
  250 m, bridge, Swiss, and physical-output gates remain required.
- Commit `57f0ba91` makes the scalable configuration unambiguous and adds a
  production conservation guard. `HICAR_WIND_ADJOINT_PROJECTION=1` now
  automatically enables the exact Galerkin hierarchy; the unsupported
  adjoint-plus-local-line-only combination cannot be selected accidentally.
  Every live adjoint wind update independently recomputes the
  volume-integrated `Bq` norm after the final single-precision wind correction
  and shared-face halo exchange, reports `relative_Bq`, and aborts before
  subsequent physics if it is nonfinite or exceeds `2e-5`. Native FGMRES now
  reports its explicit `||b-Ax||/||b||` alongside the unchanged independent
  true-residual target. The legacy and adjoint local four-rank suites remain
  green; the adjoint suite automatically builds six exact levels, returns
  status zero, and retains the `7.84e-6` matrix and `1.01e-6` independent
  constraint ratios. Reproducible Balfrin jobs now encode the one-node GPU
  unit gate (`debug`), the 20 km 250 m regression (`normal`), and the
  140 km/701 x 701 bridge (`normal`). Target-stack execution is pending
  restored passwordless `ssh balfrin`.
- Commit `ac5e310c` hardens acceptance evidence without changing a numerical
  tolerance. The four-rank adjoint unit test now compares the production
  conservation-norm routine against a separately coded volume-weighted
  calculation before and after correction and fails above `2e-6` relative
  disagreement; both legacy and adjoint suites pass locally. A shared batch
  log gate examines every FGMRES and conservation record, rejects malformed
  or nonfinite values, any residual above `1e-5`, any constraint above its
  target, and any printed target looser than `2e-5`; the 250 m and bridge jobs
  use it. Positive and negative parser fixtures, shell syntax checks, and
  whitespace checks pass.
- `scripts/validate_hicar_wind_output.py` provides a bounded-memory physical
  output gate for Swiss runs: it checks required variables and
  stagger shapes, 80 levels, finite fields, broad engineering ranges,
  exact expected record count, positive monotone height, decreasing pressure,
  monotone time, and records hashes in an atomically written JSON report. Its
  five unit tests pass, as do two geometry and five static-terrain tests. It
  also passes a known-good 514 MB,
  80-level 250 m HICAR output. This is an engineering sanity gate, not a
  substitute for scientific comparison against ICON and observations.
- Target NVHPC 24.5 / Cray MPICH 8.1.30 / CUDA 12.3 / NCCL validation passes
  the complete staged sequence; the current documented branch tip is
  `79c87b86`. Rebuild `4926997` and four-GPU
  unit job `4927001` passed; the unit retained `7.3577e-6` matrix-relative
  and `9.0531e-7` independent constraint-relative errors. The revised 250 m
  regression `4927002` completed its 24-hour run in 1m09s with minimum
  Jacobian `0.55131`, minimum interface thickness `13.521 m`, every solver
  and conservation record accepted, output present, and about 1.85 GB peak
  step RSS. The revised 701 x 701 bridge `4927046` completed 30 minutes in
  1m28s with minimum Jacobian `0.24726`, minimum interface thickness
  `12.508 m`, eight exact levels, worst residual `9.4734e-6`, worst
  conservation ratio `7.8605e-6`, and about 8.16 GB peak step RSS. These
  smaller cases remain staged regressions, not national proxies.
- Commit `cd3089c2` adds the actual SLEVE-geometry gate and selects the
  window-5/10-cycle internal terrain split while preserving the static DEM.
  The independent geometry validator predicts HICAR's constructed national
  grid to about `6e-6` in minimum Jacobian and `5e-5 m` in minimum mass-level
  spacing. The old split's initial output proved the failure was physical
  geometry, not Krylov convergence: Jacobian `-0.096514`, adjacent `z`
  difference `-8.3369 m`, and `w_grid=-4914..3347 m/s`.
- The four-node national run `4927095` completed its model step in 6m10s
  (model timing 328.3 s) on 16 compute GPUs plus four CPU I/O ranks. It built
  nine exact levels with `R A P` errors near `4e-16`; terminal collective and
  physical solves returned status zero. Initial solve/refinement relative
  residuals were `2.5403e-6`/`2.6617e-6`; first-timestep values were
  `9.3370e-6`/`4.1405e-6`; the maximum independent conservation ratio was
  `1.2488e-5`. Minimum Jacobian was `0.17194`, minimum interface thickness
  `12.257 m`, timestep `3.79 s`, and peak compute-step RSS about 30.1 GB.
  The model log passes the complete post-hoc acceptance parser. The Slurm
  batch itself returned 1 only because the original wrapper expected two
  output filenames; HICAR appends both records to one start-time-named file.
  Commit `72d277ee` corrects that publication check.
- Bounded-memory validator job `4927159` passed the 21,732,703,678-byte,
  two-record national output and published an atomic ready report. Output
  SHA-256 is
  `93c91adf871487e75fda7708ec92c79cc657ddbc0892d12ece180929a351f5a2`.
  All required fields are finite; minimum adjacent `z` spacing is
  `12.9355 m`; pressure strictly decreases; ranges include
  `u=-6.54..30.07`, `v=-7.19..16.01`, `w=-11.04..9.15`, and
  `w_grid=-23.19..16.01 m/s`. This closes the national numerical/engineering
  acceptance gate. Multi-day scientific verification against ICON and
  observations remains separate.
- The continuous seven-record REA-L forcing series from 00--06 UTC is
  publication-qualified at
  `$SCRATCH/icon_hicar/case_studies/swiss_200m/forcing/rea_l_ch1/forcing_20100101_0000_0600.txt`.
  Every record has an exact hourly valid time, complete HICAR schema,
  consistent HFL/HHL geometry, coverage, checksum, source manifest,
  validation report, and ready marker.
- Six-hour national production-candidate job `4927314` passed on four
  `normal` nodes (16 compute GPUs plus four CPU I/O ranks). Batch wall time
  was 1,411 s, HICAR reported 1,360.221 s, and peak task RSS was 33.62 GiB.
  Minimum Jacobian/interface thickness were `0.17194`/`12.257 m`; worst
  solver residual and independent conservation ratio were `9.337e-6` and
  `1.36e-5`. Output validator `4927315` passed both complete records:
  21,732,703,678 bytes, SHA-256
  `d42bd0916f0612e711c632623997e8d79ab86e587618a9cd70064941cf3d83c3`,
  minimum vertical spacing `12.9355 m`, strictly decreasing pressure, and no
  nonfinite required values.
- Height-aware source comparison `4927397` passed on 1,158 Swiss columns and
  about 90,000 paired samples per field at both endpoints. At hour 6,
  correlations were `0.99999997` pressure, `0.99956` temperature, `0.99734`
  humidity, `0.99980`/`0.99877` horizontal wind, and `0.5990` vertical wind;
  vertical-wind RMSE was `0.0488 m/s`. The consolidated PASS manifest is
  `case_studies/swiss_200m/validation/production_6h_qualification_4927314.json`.
  It refreshes the initial 100 m planning envelope to 16 nodes/64 GPUs, a
  two-hour request, and about 81 GiB for two full records; these remain
  estimates pending actual 100 m geometry, initial-solve, conservation, and
  capacity gates.
- Fixed alpha 0.2 diverges; alpha 1.0 reproduces dynamic alpha. Stronger local
  sweeps, 60 levels, restarted BiCGStab, terminal agglomeration, and rank
  deflation were rejected. An overlapping distributed-Jacobi preconditioner
  was also rejected on the four-node target run: at 2,500 iterations its
  residual rose from `57.7411` to `176.0664` (and 3/4-sweep retries ended at
  `70.5526`/`105.0954`). Do not submit further Swiss namelist sweeps.

## REA-L-CH1 20-year streaming production

- The authoritative Balfrin FDB archive contains an unbroken daily 00 UTC
  cycle series from 2005-01-01 through 2024-12-31 (7,305 cycles), with steps
  0--24. Use the valid hour's own date/cycle and `step=hour`; midnight uses
  the new cycle's step 0. A direct boundary comparison found only tiny
  previous-step-24/new-step-0 differences, but the records are not identical.
  Canonical inventories are
  `validation/rea_l_archive_inventory.json` and
  `validation/rea_l_cycle_boundary_20091231_20100101.json`.
- The live fused producer qualification passed for three 2020-07-01 records:
  61--85 s per record with two concurrent `pp-short` workers, about 674 MB
  native dynamic FDB traffic per hour, and 274.5 MB retained 200 m HICAR
  forcing per hour. Native GRIB and converter work are transient. The
  publication report is
  `validation/streaming_forcing_qualification_20200701.json`; an independent
  02--04 UTC continuation producer also passed.
- The production unit is seven simulated days (169 forcing timestamps), with
  bounded CPU production, exact-end restart, daily HICAR output files,
  losslessly verified level-1 compression, and guarded forcing retirement
  only after model/restart completion. The implementation and lifecycle
  contract are under `case_studies/swiss_200m/streaming/`.
- The completed 72-hour national summer qualification updates the provisional
  200 m planning base to 10.64 h per seven-day chunk on four nodes/16 GPUs,
  44.4k node-hours and 177.7k GPU-hours for 2005--2024. The capacity-scaled
  100 m base is 21.29 h per chunk on 16 nodes/64 GPUs, 355k node-hours and
  1.42M GPU-hours; its 15.96--31.93 h range still requires a live 100 m
  capacity/restart benchmark. Running all 20
  yearly chains simultaneously would require 80 nodes at 200 m and 320 at
  100 m, so neither fits the 46-node `normal` partition. At theoretical
  continuous near-exclusive capacity, 200 m permits eleven chains in two
  waves and takes 37.0--69.4 days; 100 m permits two chains in ten waves and
  takes 347--694 days. These are optimistic lower bounds before queueing,
  retries, maintenance, competing production, or archive transfer. Eleven hourly
  2-D fields plus repeated lat/lon require 26.51/105.96 TiB raw at 200/100 m.
  The live level-1 deflate ratio is 0.606, giving 16.06/64.21 TiB; conservative
  planning ranges are 13.25--21.20/52.98--84.77 TiB. Seven-day forcing rings
  are about 43.2/172.8 GiB. The canonical calculation is
  `validation/rea_l_20year_resource_estimate.json`.
- REA-L FDB contains the year-chain initialization ingredients `SKT`,
  eight-level `T_SO`, eight-layer integrated `W_SO`, `W_SNOW`, and
  `RHO_SNOW`. Soil fields require conservative depth conversion/remapping;
  snow height is derived as `W_SNOW/RHO_SNOW`. The audited contract is
  `validation/rea_l_land_state_inventory.json`. Follow-up `pp-short` audit
  `4929313` resolved the GRIB geometry: `T_SO` is defined at
  `0/0.005/0.02/0.06/0.18/0.54/1.62/4.86 m`, while `W_SO` is integrated
  over bounded layers `0--0.01/0.01--0.03/.../7.29--21.87 m`.
- The first REA-L-derived national land-state pipeline now passes. Producer
  `4929477` retrieved and fieldextra-regridded the five fields for
  2020-07-01 00 UTC in 15 s, publishing a 7.8 MB regular-grid product with
  separate temperature-depth and water-layer coordinates. Builder `4929478`
  completed in 25 s and published a 121 MB HICAR static initialization:
  temperature is interpolated to NoahMP layer midpoints, integrated soil
  water is overlap-conservatively remapped to the
  `0--0.1/0.1--0.3/0.3--0.7/0.7--1.5 m` layers, and snow height is
  `W_SNOW/RHO_SNOW`. No density fallback was needed; all land values are
  finite. Ranges are surface temperature `265.10--300.78 K`, soil
  temperature `264.30--303.40 K`, VWC `0--0.5025`, SWE
  `0--1566.86 kg m-2`, and snow height `0--3.917 m`. Canonical manifests are
  `validation/rea_l_land_state_20200701_0000.json` and
  `validation/rea_l_land_initialization_20200701_0000.json`.
- The Swiss renderer and stream controller now have an explicit
  `qualification` profile at configurable output intervals. It adds snow,
  canopy, soil-layer/column, runoff, sensible/latent/ground heat, radiation,
  skin-temperature, and albedo diagnostics without changing the bounded
  routine-production profile. The model-chunk validator is profile- and
  interval-aware and scans every qualification field for masked/non-finite
  values while recording ranges. Soil and runoff ranges use active USGS soil,
  excluding water class 16 and permanent snow/ice class 24; all saturated
  columns and small negative subsurface fluxes in the first smoke run were
  independently localized to class 24 rather than active soil.
- The two-hour national HICAR consumption smoke now passes with the
  REA-L-derived initialization and qualification profile. HICAR explicitly
  read the land variables and supplied snow height, all initial and hourly
  wind solves met the true-residual and mass-constraint gates, and the run
  completed in 663.35 s. It published three hourly records (2.626 GB raw)
  and a 42.368 GB exact-end restart; active-soil VWC remained
  `7.99e-5--0.4872`, column water `0--677.53 kg m-2`, and subsurface runoff
  `0--0.0402 mm s-1`. The canonical completion and class reports are
  `validation/rea_l_land_initialization_smoke_20200701_00_02.json` and
  `validation/land_init_smoke_surface_classes.json`. The class-aware
  scientific evaluator also passes this smoke: after the intentionally empty
  initial column diagnostic, layer-derived and reported soil water agree in
  the 10 km interior to better than `8.0e-5 kg m-2`; all required fields are
  finite and the surface-energy residual is about `1.06 W m-2`. Its water
  residual is diagnostic only because groundwater/lakes are omitted and
  hourly instantaneous runoff is trapezoid-integrated.
- Both 72-hour event forcing publications pass with 73 hourly records. Each
  event also has a verified 25-record, three-hourly REA-L surface-reference
  stream for pressure, temperature/dew point/derived humidity, wind,
  interval precipitation, snow, and source terrain. Native GRIB is transient;
  the compact sidecars total 95.6 MB (summer) and 99.8 MB (winter).
  Cumulative precipitation differencing records and clips only quantization
  noise within `0.01 kg m-2`; `W_SNOW` is parameter `500044`, while
  `502336` is `SKT`. The first four-node/sixteen-GPU summer event reached
  52 simulated hours with clean SLEVE, exact-Galerkin, terminal-solve,
  true-residual, and adjoint-conservation gates, then stopped in the RRTMGP
  cloud-fraction OpenACC kernel with `CUDA_ERROR_LAUNCH_FAILED`. It produced
  no valid completion or restart publication. The isolated repair replaces
  dynamically private 80-level per-column arrays with a level-local scalar
  cloud-fraction routine and separate column-maximum reduction; local tests,
  a target-stack build, the full four-GPU unit/halo suite, and a 30-minute
  production-physics smoke pass. The repaired source is
  `3bbc926b891b466e6957279c61a73c567533dad9` locally and the isolated Balfrin
  validation source is `2ea31109801a2477a946840693934318f8d50c95`.
  A 701-by-701-cell, 200 m, 80-level RRTMGP bridge passes on the production
  stack, and the small parent-versus-repair case is bitwise identical for all
  nine compared output fields. The national pre-failure trajectories are not
  bitwise identical across allocations, but their first 19 simulated hours
  have identical solver iteration counts and gate decisions, with logged
  relative differences no larger than `4.8e-4`. National summer recovery job
  `4932691` completed all 72 simulated hours on four normal-partition nodes:
  HICAR reported 16,421.08 s, 146 accepted wind solves, 73 passing
  adjoint-conservation gates, no fatal/CUDA marker, and 35,222,364 KiB peak
  task RSS. Its compute step completed, but the batch wrapper initially failed
  after the model because the validator incorrectly capped accumulated
  snowfall and graupel at 10 kg m-2; the observed 72-hour maxima were 31.71
  and 190.10 kg m-2 within 713.40 kg m-2 total accumulated precipitation.
  The corrected contract treats all three as accumulated amounts and applies
  the same broad 10,000 kg m-2 corruption ceiling. Immutable validator
  snapshot `acb1d069...` and recovery job `4934458` revalidated the preserved
  artifacts without a model rerun: 25 three-hourly records (14.10 GB) and the
  exact-end 42,368,218,492-byte restart are ready, with restart SHA-256
  `4f8ea50c0f2774163e06e596cf5bda23acbed8cb3d2aae2fcc8976a8a09ab879`.
  Independent summer physical/REA-L, solver, SwissMetNet, OGD, exact-end
  restart, and three-checkpoint audit jobs all pass; coverage is 170 station
  sites at all 25 model times, two exact RhiresD windows, and 24 SIS times.
  The frozen standalone summer assessor passes every catastrophic-degradation
  screen. This is not broad added-value evidence: HICAR slightly improves
  station pressure and vector-wind RMSE, but is worse than REA-L for several
  temperature, humidity, and precipitation metrics. The tightest screen is
  RhiresD precipitation (HICAR RMSE 11.60, REA-L 6.07, limit 12.14 kg m-2),
  leaving only 0.54 kg m-2 margin.
  The current water diagnostic is not qualified: HICAR output metadata labels
  `runoff_surface`/`runoff_subsurface` as `mm s-1`, while the soil-model path
  describes the copied NoahMP values as millimetres per soil timestep, and
  `evaluate_scientific_event.py` currently trapezoid-integrates them as rates.
  The preserved summer active-soil report therefore integrates runoff to
  `494.07 kg m-2` and reports a `-492.93 kg m-2` residual but still passes
  because water closure is diagnostic-only. Source tracing confirms that
  NoahMP multiplies its runoff rates by the 300 s soil timestep before HICAR
  copies the last-step depths; both preserved files attest that timestep and
  a 10,800 s output interval. Algebraic reconstruction gives approximate
  all-active/interior runoff `1.638/1.647 kg m-2` and residuals
  `-0.504/-0.510 kg m-2`, but elevation-stratum residuals range from about
  `-2.02` to `+12.89 kg m-2`; sparse instantaneous sampling and omitted
  groundwater stores prevent an exact closure claim. Before month compute,
  HICAR must publish restart-persistent, exactly bounded online runoff and
  evaporation flux totals (with proven precipitation compatibility), expose
  the missing groundwater stores, correct the snapshot metadata, and pass
  nonzero restart-continuity/equivalence tests. The event source pin remains
  `2ea31109`; the qualified diagnostic-only child must receive a separate
  month-stage source identity. Until that P0 gate passes, any automatic
  paired-event `GO` authorizes planning only and must not authorize month
  compute. The future-stage water-observable implementation exists locally
  and in isolated Balfrin child
  `452f7245c3cd47957a0ee01c056b604712372598`, a direct three-file child of
  `2ea31109801a2477a946840693934318f8d50c95`. It corrects the legacy snapshot
  metadata, appends restart-persistent cumulative surface/subsurface runoff
  and signed net evaporation, and exposes the groundwater closure stores
  without renumbering existing variable IDs. Clean NVHPC/NCCL build
  `4934969` and child-only 701 x 701 bridge `4934979` pass. National A/B and
  segmented model job `4935158` also completed, but its canonical source
  qualification is `FAIL`: 23 pre-existing cold-start output fields were not
  bitwise equal, restart continuity failed, and this dry two-hour case did
  not exercise nonzero runoff. It does not qualify the month source.

  A separate bounded restart-initialization investigation then tested two
  direct children of the unchanged event source. Candidate
  `62f16b5c76ef65d8249e38de767e591415c533a2` guarded restart T2/Q2 and
  soil-state initialization. Candidate
  `7fa268f1ae61d7be8567f32d85bf55557b104915` additionally passed restart
  semantics into YSU and preserved its initialized tendency arrays; it
  changes only `src/physics/lsm_driver.F90` and
  `src/physics/pbl_driver.F90`. Its clean target build `4936312`, 701 x 701
  bridge `4936313`, and three-leg Swiss national model job `4936315` pass
  operationally; the national job completed in 34m30s and its restarted leg
  used 53,827,104 KiB peak task RSS. Evidence job `4936365` published
  `restart_initialization_qualification.json` with SHA-256
  `0e9aa32457c4b5f64224a23b3ee10fad0e19cdaaae497ded98067b125da3a29f`
  and no ready marker. The result is `FAIL`: the tolerance-based cold-start
  comparison passes with three warnings, but bitwise cold-start equality
  still has 23 mismatched fields and the exact solver-line comparison differs
  in final digits. More importantly, restarted output still fails for
  `canopy_water`, `hus2m`, `runoff_surface`, and `runoff_subsurface`;
  end-restart state still fails for those related canopy/humidity fields plus
  `Sliq`, `coeff_momentum_drag`, `lsm_last_precip`, and
  `tend_th_lwrad`. The counts and maxima are materially unchanged from the
  first candidate, so the extra YSU guards do not address the dominant
  defect.

  Source tracing also confirms that `allocate_noah_data` resets Noah-MP
  `ITIMESTEP` to 1 on every restart. The current one-hour boundary happens to
  preserve the tested soil-cycle phase, so this does not explain the present
  failure set, but arbitrary production segment lengths remain unsafe until
  the counter is restart-persistent or reconstructed exactly. Before another
  national allocation, use an initialization-only bridge diagnostic to
  compare checkpoint state immediately before and after each physics
  initializer, then validate a bounded correction on the bridge. Keep
  `month_expected_hicar_commit` null and all month/100 m successors held.
  The combined local coordinator suite passes 201 tests.
  The repaired cloud-fraction kernel therefore passes the complete summer
  event, but sustained production remains blocked on winter, the independent
  hour-48-to-72 restart trajectory, and the paired verdict. The conditional
  winter is replacement job `4934501`. The first replacement overlap
  allocation (`4934502`) failed before HICAR in two seconds because its
  wrapper directly executed the deliberately read-only stream-runner
  snapshot. The wrapper now invokes the unchanged hash-pinned runner through
  Bash, with a focused regression test; replacement overlap job `4934663`
  preserves the audited hour-48 checkpoint and both active model jobs pin
  `acb1d069...`.
  The canonical recovery record is
  `validation/radiation_cloud_fraction_recovery_qualification.json`. The
  bounded intermediate-checkpoint validator also passes a real 42.37 GB,
  136-variable restart on `pp-short` (job `4933132`, 40 s, 74.8 MB peak
  RSS), including its 0.432013 s encoded-time offset and whole-file checksum;
  its canonical smoke report is
  `validation/restart_checkpoint_validator_smoke_20200701_02.json`. The live
  national recovery then advanced beyond its first 24-hour boundary and job
  `4933135` independently validated the 42,368,218,492-byte, 136-variable
  checkpoint at exactly `2020-07-02 00 UTC`, pinned to commit `2ea31109`,
  with SHA-256
  `9fa576d8814bf69d090d2ba7bc57370dfd8bfe8d31ef9ed1dc7376934c521e5a`.
  After the model advanced nine hours past its second boundary, job `4933153`
  independently validated the 42,368,218,492-byte, 136-variable checkpoint at
  exactly `2020-07-03 00 UTC`, pinned to the same repaired commit, with
  SHA-256
  `c26baa2f151a21c587c220e5f283acfb26b449809e9b7df37e216f7d5af9b710`
  (42 s, 69,988 KiB peak RSS). The post-publication dependency and runtime
  audit is
  `validation/radiation_cloud_fraction/downstream_chain_recovery_preflight.json`;
  the earlier preflight remains historical and is explicitly superseded.
  The replacement winter/overlap jobs use the same repaired source commit and
  the read-only corrected validator snapshot; their validators, trajectory
  comparison, paired verdict, and planning-only successors remain strictly
  `afterok`-gated. The paired assessor has also been
  checked together with the restart, trajectory, physical,
  solver, REA-L, SwissMetNet, and OGD Python entry points: all eight local and
  Balfrin CLIs accept exactly the options their submitted wrappers use. The
  pending paired-event assessor now
  also requires each model's provenance block to be `PASS` and its source
  commit to equal the repaired commit frozen in the scientific plan; a
  clean-but-different event cannot authorize either next stage. Status-only
  OGD reports are no longer sufficient: each 72-hour event must expose at
  the exact two 06--06 UTC RhiresD windows, three TabsD days, and 24
  post-initial SIS times derived from its frozen axis; duplicate, shifted, or
  sparse inventories cannot promote. Event-level gridded non-degradation is
  also frozen:
  interior TabsD RMSE may exceed REA-L by at most 2 K, while RhiresD must
  satisfy `HICAR RMSE <= max(2 * REA-L RMSE, REA-L RMSE + 2 kg m-2)`.
  The gate also accepts exactly one named summer and one named winter event;
  their model and station timestamps must equal the frozen inclusive
  three-hour axes, so two arbitrary 25-record products cannot promote.
  Physical-budget, REA-L, SwissMetNet, and OGD reports must also identify the
  exact model chunk they validate, preventing cross-event report reuse.
  Event-level restart audit jobs `4933477`/`4933478` are now afterok-gated on
  summer/winter respectively; each independently validates and hashes the
  24/48/72-hour checkpoint files, and paired verdict `4932702` depends on
  both audits and requires exactly those three passing boundaries. A separate
  early restart-trajectory gate reuses 25 already-published forcing records
  without new FDB retrieval or fieldextra conversion. Job `4934663` will
  independently continue from the audited summer 48-hour checkpoint through
  hour 72; comparison job `4933658` must pass all eight subsequent
  three-hourly qualification records before the paired verdict can promote.
  This event-scale test is an early defect screen and does not replace the
  month-stage multi-day uninterrupted overlap. The
  canonical audit is
  `validation/radiation_cloud_fraction/downstream_chain_recovery_preflight.json`.
  Independent station access is now
  operational: JRetrieve PROD station group `SMN` published quality-category
  4 hourly event streams for 158 station abbreviations (170 summer and 168
  winter measurement sites). Raw DWH use-limitation-50 CSV remains internal
  in event scratch; checksum-bearing manifests are retained locally. A
  bounded evaluator samples the nearest HICAR cell and bilinear REA-L field,
  reports both against the identical station samples, applies explicit
  temperature/pressure height corrections, aligns three-hour precipitation,
  and stratifies results by elevation, 10 km boundary distance, and a
  documented 5 km terrain-exposure diagnostic. Station validations
  `4932696`/`4932700` are dependency-gated on their model runs; pipeline PASS
  means valid access/sampling/metrics, not acceptable scientific skill.
  MeteoSwiss public OGD archive access is also operational: job `4930892`
  published checksum-verified 2020 RhiresD, TabsD, SIS, and SIS-No-Horizon
  assets (about 570 MB total). Event comparisons aggregate HICAR and REA-L to
  the identical 1 km RhiresD grid and exact 06--06 UTC window, and compare
  their trapezoidally sampled three-hour temperature over each 00--24 UTC
  TabsD day. HICAR shortwave is compared on the approximately 2 km SIS grid
  with an explicit hourly-versus-three-hourly representativeness
  qualification. Summer/winter OGD
  validators `4932697`/`4932701` are dependency-gated on their model runs.
  Historical CombiPrecip/CPC remains unavailable but is not required while
  RhiresD is operational. The
  event-to-month and event-to-100 m capacity criteria are now frozen in
  `config/scientific_pilot_plan.json` before completed event scores are
  available. They require both seasonal report stacks, 25 output/station
  records, interior surface-energy closure, minimum station sample counts,
  gridded precipitation/radiation reports, and predeclared catastrophic-
  degradation margins relative to REA-L. The paired assessor is dependency-
  gated as job `4932702`; only a
  `GO_MONTH_AND_100M_CAPACITY_GATE` verdict permits those next engineering
  planning stages (`4932768`/`4932769`), and it never authorizes an annual,
  20-year, or 100 m scientific campaign. The winter REA-L-derived
  initialization also passes without a snow-density
  fallback; ranges are surface temperature `259.08--284.08 K`, soil
  temperature `262.48--283.89 K`, active-land VWC `0--0.4862`, SWE
  `0--1038.71 kg m-2`, and snow height `0--3.993 m`. The winter model is
  held until a repaired summer event passes so escalation stops
  automatically on failure. Independent yearly chains remain unqualified
  until both event pilots and restart/spin-up equivalence pass.
- The conditional July 2020 month stage is now executable but not launched.
  Its gate-enforced planner publishes five 7/7/7/7/3-day restart-linked
  segments with 249 unique three-hourly qualification records, a declared
  seven-day spin-up and 24 retained days, plus an independent uninterrupted
  eight-day run from the July 8 restart across the July 15 boundary.
  Predeclared absolute-plus-relative tolerances require every qualification
  field to match for 24 post-boundary hours; equality only at the restart
  instant is insufficient. The dry-run-by-default submission DAG now has 50
  jobs: bounded forcing arrays, a 249-record REA-L reference array,
  whole-month SwissMetNet retrieval, sequential GPU segments with every solve
  audited before the next segment, lossless compression, the overlap run, a
  trajectory gate that blocks segments four and five on failure, four
  parallel whole-month scientific validators, a retained-period class drift
  screen, and a final month-to-annual assessor. The assessor requires exact
  249-record coverage, every model/solver/compression publication, physical
  budgets, frozen HICAR-versus-REA-L station and OGD degradation limits, and
  signed attribution of every large nearly monotonic post-spin-up tendency.
  It also requires the exact station/model axis, 31 unique TabsD days, 30
  unique RhiresD windows, and 247 unique SIS matches contained in the frozen
  month axis; duplicate dates or a status-only radiation report cannot
  promote.
  An unexplained tendency stops escalation. An otherwise passing month is
  held explicitly until both the application-specific absolute quality/weight
  contract in `config/observational_validation_contract.json` and
  `config/production_archive_contract.json` change from `UNRESOLVED` to
  approved. Relative non-degradation against REA-L is necessary but cannot
  substitute for application limits. Archive approval requires a durable
  destination with owner/quota, measured transfer, and a published restore
  drill. Approved MeteoSwiss
  guidance classifies reproducible climate grids as facultative retention.
  Balfrin exposes candidate `msclim` store/tape paths, but the current 2026
  tape plan is only 10 TB while the existing 200 m twenty-year estimate is
  13.25--21.20 TiB for compressed routine fields plus 0.77 TiB for annual
  checkpoints, before validation and wind products. The data owner and CSCS
  division SPOC must therefore approve a larger project-owned envelope before
  any transfer drill. The month decision can authorize only an annual cycle,
  never 20-year production. It cannot plan or submit without the
  checksum-verified paired event authorization. Current planning-only jobs
  `4932768` (month) and `4932769` (100 m) depend on paired verdict `4932702`
  and independently require its exact `GO_MONTH_AND_100M_CAPACITY_GATE`
  decision. Both successors are on reversible user hold so they cannot publish
  plans from the superseded pre-water-budget schema; release requires
  hash-verified future-stage P0 runtime synchronization after the active event
  verdict is frozen. They publish plans only; no month or 100 m compute job
  has been submitted. The scientific plan, month planner, submitter, every main/overlap
  model job, and final month assessor now pin the 31-day stage to repaired
  commit `2ea31109801a2477a946840693934318f8d50c95`; a clean but different
  checkout cannot enter or pass the gate. The future month/annual runner now
  publishes a production-provenance block that re-verifies no tracked changes
  and no untracked build inputs under declared source/configuration paths
  (while permitting unrelated untracked runtime logs), the full source commit,
  executable SHA-256 before/after execution, exact archived chunk plan and
  forcing publication, static-domain SHA-256, and model-log SHA-256. Both
  higher-stage assessors refuse promotion when
  that block is absent or fails, or when segments mix source commits,
  executables, or static domains. The active event/wind chains remain on their
  frozen remote code and were not changed. The month and 100 m submitters now
  also reject a stale runner/validator/assessor before `sbatch` and record the
  referenced Slurm scripts plus critical Python runtime path/size/SHA-256
  manifest in previews and receipts. The month DAG now also executes
  hash-guarded forcing retirement after each of its five main model
  publications and the uninterrupted overlap, withdrawing ready markers
  before payload deletion and publishing a durable retirement report. The
  month assessor requires all six reports and their exact planned record
  counts before promotion. Four additional lifecycle jobs retire superseded
  main-chain restarts only after their adjacent successors pass; the July 8
  restart is retained through the uninterrupted-trajectory comparison, and
  the final main and overlap checkpoints remain. Promotion requires all four
  restart-retirement publications. The combined coordinator suite is 179
  passing tests.
- The annual-to-20-year decision is now mechanically frozen locally, but no
  annual plan or compute is authorized. The 2019-10-01--2020-10-01 contract
  requires 2,929 unique three-hourly records, 366 TabsD days, 365 complete
  RhiresD windows, 2,927 SIS matches, at least 95% coverage in every season,
  four restart-trajectory boundaries, and independently initialized DJF/JJA
  overlaps with at least 21 retained days. The OGD retriever supports
  cross-year periods and rejects a ready manifest for a different selection;
  SwissMetNet and OGD comparators now emit seasonal metrics. Annual
  observation coverage is evaluated only after the station, TabsD, RhiresD,
  and SIS timestamp collections are proven duplicate-free and contained in
  their exact frozen axes, so repeated or out-of-period matches cannot inflate
  a seasonal fraction. The bounded
  `validation/assess_scientific_annual.py` can authorize only
  `GO_20_YEAR_200M_PRODUCTION` after all relative, absolute-quality, drift,
  recovery, archive-restore, and immutable-release screens pass; it never
  authorizes 100 m science. Its release screen now hashes the actual published
  source snapshot, executable, static domain, configuration bundle, and
  annual-plan copy, requires them under the approved destination and matched
  to the annual segment identity, and its archive screen hashes the actual
  transfer manifest and matches the approved destination; well-formed hash
  strings alone cannot pass. A dry
  planning-only annual planner is also frozen:
  after `GO_ANNUAL_CYCLE`, approved contracts, and a new 2019-10-01 REA-L
  land initialization, it publishes 53 main segments, four seasonal restart
  overlaps, and two 28-day DJF/JJA initialization overlaps, but has no annual
  submitter. The generated annual plan freezes repaired source commit
  `2ea31109801a2477a946840693934318f8d50c95`; its production assessor
  additionally requires every segment's provenance block to pass and to match
  that exact commit, so a legacy or clean-but-different completion cannot
  authorize the 20-year campaign. The planning-only annual planner and
  assessor are hash-verified under the canonical Balfrin tree
  (`d63c597f...`/`b82d29f7...`); no annual compute is authorized before the
  month gate and allocation review.
- The actual national 100 m static grid now passes the independent frozen
  80-level SLEVE geometry gate. Balfrin job `4931258` evaluated all
  14,989,841 terrain cells with the configured 5-cell, 10-cycle terrain split:
  minimum mass Jacobian `0.260878`, minimum interface thickness `12.566 m`,
  and minimum mass-level spacing `13.422 m`, versus required
  `0.1/5/5`. The published report is
  `case_studies/swiss_100m/validation/sleve_geometry_80l.json`. The earlier
  July 100 m jobs stopped on launcher or corrupted legacy forcing-list paths
  before numerical initialization and provide no capacity evidence. The
  replacement gate is frozen in
  `case_studies/swiss_100m/config/engineering_capacity_gate.json`: two
  16-node/64-GPU two-hour segments share the restart-boundary forcing record,
  write and reread an exact-end restart, sample every GPU and node once per
  second, audit every wind solve, compare all routine fields at the boundary,
  and publish measured forcing/model/restart/output/validation costs. The
  current planning-only job is `4932769`, dependency-gated on paired verdict
  `4932702`; it cannot submit compute. The plan, submitter, runner, and final
  assessor now independently pin both capacity segments to repaired HICAR
  commit `2ea31109801a2477a946840693934318f8d50c95`, so a clean older checkout
  cannot enter or pass the gate. The ten-job capacity DAG remains unsubmitted
  and its submitter is dry-run by default. Passing qualifies engineering
  capacity only, never 100 m science or production. The final assessor now
  independently re-hashes the frozen
  gate config, paired-event authorization, geometry report, and actual static
  domain; requires the exact 16-node by four-GPU memory-sampling topology and
  15% headroom; requires all nine accounting labels, positive output/restart
  bytes, complete HICAR timers, and measured model/restart/validation walls;
  and rejects a plan whose copied commit or static identity diverges from the
  frozen config. The synchronized Balfrin config/assessor hashes are
  `915afe8c...`/`c2807dd2...`.
  Live partition inspection confirms `normal` has 46 nodes and
  a 24-hour maximum. The summer-calibrated seven-day 100 m base segment is
  21.29 h and its 15.96--31.93 h uncertainty range extends beyond 24 h;
  shorten production
  segments if the capacity gate does not leave adequate wall-time margin.
- The two-segment live 200 m gate passes. The 00--02 UTC model ran in
  661.61 s and published a 42,368,218,489-byte exact-end restart. The restart
  continuation read that state, ran 02--04 UTC in 716.20 s, emitted unique
  03/04 UTC history records, and published a second restart of the same size.
  Ten routine diagnostics shared by history and restart are bitwise identical
  at 02 UTC. Completion reports and the boundary comparison are under
  `case_studies/swiss_200m/validation/`.
- The first live routine file exposed a repeated 1.2 GB static 80-level `z`
  field. HICAR `cef7e3d6` prevents restart-only 3-D state from forcing `z`
  into 2-D history, and `16bdb27b` permits the resulting zero-count 3-D
  output buffer. The corrected two-record file is 360,387,970 bytes and its
  losslessly verified compressed copy is 218,364,591 bytes. Compression took
  12 s and therefore has ample throughput margin.
- Each 200 m restart is 39.46 GiB; retaining all seven-day boundaries would
  consume 40.23 TiB (160.83 TiB at capacity-scaled 100 m). Keep two rolling
  boundaries plus selected checkpoints. The guarded retirement verifier
  passed the live pair in preserve-checkpoint mode.
- HICAR `70b7a57d` fixes layered soil-input metadata; `cef7e3d6` and
  `16bdb27b` fix the routine-output storage path. Local compilation and
  Balfrin target builds passed. The remaining campaign gates are a live
  100 m capacity/restart case, scientifically qualified REA-L-derived
  land/snow initialization and chain spin-up, and a durable archive contract.

## Published long-duration evidence

- Published HICAR evidence reaches seasonal, catchment-scale integrations but
  not multi-year or Switzerland-scale integrations. Berg et al. (2024,
  doi:10.3389/feart.2024.1393260) used HICAR forcing at 50, 100, and 250 m
  from October 2016 through July 2017 over a 175 km2 Davos domain. Reynolds
  et al. (2024, doi:10.5194/tc-18-4315-2024) ran the two-way-coupled
  HICARsnow system from October 2016 through May 2017 at 50 m, nested through
  250 m and 1 km domains.
- The seasonal studies demonstrate feasibility and useful snow-pattern
  skill, not absence of drift. They report systematic errors including
  high-elevation wet and valley dry biases in HICAR-forced snow simulations,
  excessive nocturnal cooling with NoahMP over snow, exaggerated thermal
  winds, and excessive high-elevation/south-facing melt linked to temperature
  and albedo biases.
- The 2007--2017 multiyear evidence is for base ICAR at 4 km (Horak et al.,
  2019, doi:10.5194/hess-23-2715-2019), with surface-atmosphere flux coupling
  disabled and precipitation as the evaluated prognostic product. It does
  not validate HICAR's hectometre-scale NoahMP soil/snow state or long-term
  surface-energy and water budgets.
- HICAR is strongly constrained rather than freely evolving: driving pressure
  and winds are used throughout the domain, while temperature and humidity
  are imposed at lateral boundaries. This reduces the risk of unconstrained
  large-scale atmospheric climate drift, but it does not prevent persistent
  forcing/physics bias or drift in soil, snow, canopy, hydrometeor, and
  surface-energy/water stores.
- Treat a long-duration scientific qualification as mandatory before the
  20-year campaign. It must distinguish numerical drift from a stationary
  model bias, test restart segmentation against an uninterrupted reference,
  and close atmospheric/surface water and energy budgets while tracking
  seasonal behavior by elevation and terrain class.

## Active constraints and next work

1. Preserve the discretely adjoint operator, manufactured/distributed
   symmetry, exact-RAP, terminal-solve, independent true-residual, actual
   SLEVE-geometry, and independently recomputed mass-constraint gates. Do not
   spend another national allocation on restart length, local sweep count,
   recycle selection, terminal accuracy, or generic AMG tuning of the retired
   nonnormal operator.
2. Keep ready markers publication-safe: write, validate, atomically rename,
   then create `<file>.ready`; record paths, options, and checksums in each
   case manifest.
3. Extend the accepted six-hour national engineering baseline to 24 h and
   add terrain-class, boundary-zone, precipitation, and independent
   observational comparisons before calling it a scientific production
   baseline.
4. Use the 81 x 81 Alpine 250 m case as a regression gate, then the 701 x 701
   hard regional bridge, then the Swiss domain. Neither smaller case is a
   proxy for national convergence. Before a national 100 m run, construct the
   actual 100 m SLEVE grid and pass a bounded geometry/initial-solve/capacity
   case using the measured 200 m resource envelope.
5. The two-segment restart/output-compression gates and the first
   REA-L-to-HICAR land-state conversion now pass. Before parallel 20-year
   production, first complete and validate the isolated radiation-kernel
   recovery, then complete the staged scientific qualification
   (summer/winter events, month, then an annual seasonal cycle), characterize
   year-chain spin-up equivalence, run the 100 m capacity/restart gate, and
   freeze the archive destination and diagnostic profile. Month/annual
   promotion and the 100 m engineering-capacity assessor also require the
   embedded production-provenance block; preserve the current event/wind
   runtime until their submitted chains settle, then synchronize the hardened
   runner/validator before any month or 100 m launch. The stream runner now
   supports an explicitly bounded restart cadence in output-record units and
   an expected-source-commit pin; use 24-hour recovery checkpoints for the
   72-hour event so a late failure is recoverable without retaining an
   excessive restart series. The isolated radiation repair is local commit
   `3bbc926b` and Balfrin event source `2ea31109`; its target build, four-GPU
   tests, production-physics smoke, bitwise parent/fix comparison, and
   701 x 701 bridge pass. Both 72-hour event integrations and their
   catastrophic-degradation screens complete, but the independently
   initialized restart-overlap trajectory fails and neither the water-budget
   child nor restart-initialization candidates are qualified. Month and
   100 m successors therefore remain held. The next finite gate is an
   initialization-only bridge diagnosis followed by a restart-equivalent
   candidate; do not spend another national allocation before that bounded
   test passes. Scratch remains a rolling workspace.
6. The HICAR-native wind-climatology engineering pathway passes through the
   Swiss 200 m restart/archive gate. HICAR commit `2999c9bd` (tracked-clean
   locally and on Balfrin) supplies CF-described `u10m`/`v10m`, mass-grid
   `u_agl`/`v_agl`/`rho_agl` at 50/75/100/125/150/200 m AGL, and `ustar`,
   momentum roughness, raw bulk Richardson number, and PBL height. Fixed
   heights use linear geometric-height interpolation without extrapolation.
   The separate `wind_climatology` renderer/validator profile preserves the
   land/energy `qualification` profile. Independent 81 x 81 errors were below
   `2.0e-6 m s-1` and `6.7e-8 kg m-3`; the committed-source 701 x 701 bridge
   independently matched below `9.5e-7 m s-1` and `6.1e-8 kg m-3`, with its
   1,207,455,751-byte qualification file reducing to an 81,437,906-byte
   surface/PBL/distribution product (14.83-to-1).

   The bounded reducer and exact merger publish vector/scalar moments,
   density-weighted cubic wind power, resolved maxima, surface/PBL
   means/maxima, 12 direction sectors, calm counts, and six nested speed
   exceedance counts at 10/50/75/100/125/150/200 m. They compose by weighted
   moments, sums, and maxima without reopening raw output. Both NetCDF
   payloads and hash-bearing JSON reports now receive publication-safe ready
   markers. HICAR's sub-second encoded-time drift is accepted only for an
   explicit interval start: every source time must lie within one second of
   the inferred cadence, then the exact axis is constructed before grouping;
   ordinary inputs remain strict.

   National cold-start job `4931362` passed in 9:29 (`417.54 s` HICAR) and
   restart-continuation job `4931363` passed its model/output/restart gates in
   10:05 (`486.96 s` HICAR). Their raw outputs were 1,111,223,047 bytes
   (three records) and 750,846,487 bytes (two new records); each
   136-variable rolling restart was 42,368,218,828 bytes. Reducer repair job
   `4931904` recovered the already valid second output after the
   canonicalization-order regression. Finalizer `4932048` exactly merged the
   two 676.7/677.2 MB hourly products into one 682,660,104-byte four-sample
   product with surface/PBL and distribution statistics; the hash-checked
   retirement dry run reached `READY_TO_RETIRE` without deleting either
   interval. The complete coordinator suite passes 178 tests.

   Before long-duration release, run a representative observational
   comparison (stations plus available mast/lidar), compare a segmented
   trajectory with an uninterrupted overlap, freeze and restore-test the
   durable archive, and decide whether persistence/joint speed-direction
   state justifies its storage cost. The current SwissMetNet feed carries
   hourly mean wind (`fkl010h0`/`dkl010h0`); retrieve the documented
   one-second hourly peak `fkl010h1` separately for any gust experiment and
   harmonize its duration explicitly. Do not preserve ICON `VMAX`. The current
   `resolved_wind_speed_max` is a maximum of HICAR samples with exact bounds
   and cadence, not a gust. Add `wind_speed_of_gust` only after a distinct
   HICAR-native 3-second method passes like-for-like Swiss observation and
   held-out upper-tail validation.

## Pre-emption and elastic-capacity constraint

- The validated workflow is restart-linked at planned segment boundaries but
  is not yet qualified for Balfrin's `preemptible` partition. Live Slurm and
  current Confluence guidance agree that `preemptible` cancels jobs after a
  `SIGTERM` with a 60-second grace period; `lowprio` is priority zero but does
  not use the same cancellation policy.
- The current model runner has no signal-aware safe stop, retry reconciler, or
  latest-checkpoint discovery. It defaults to one exact-end restart per
  seven-day segment, writes model NetCDF artifacts directly to final paths,
  rejects a dirty fixed run directory on retry, and leaves `afterok`
  successors blocked after cancellation. Slurm requeue is therefore not
  equivalent to model resume.
- Before pre-emptible production, use immutable attempt directories, select
  only closed and independently validated hash-pinned checkpoints, synthesize
  a remainder plan from the selected checkpoint, validate the union of output
  times across attempts, and let a persistent external reconciler resubmit
  retryable work. Treat the 60-second signal as a best-effort graceful-stop
  optimization; recovery must also survive an untrappable kill from the last
  previously published checkpoint.
- Decouple the forcing/archive chunk from the shorter execution attempt.
  Choose the checkpoint and attempt duration from measured restart I/O cost
  and acceptable lost work. Maintain a backlog of ready attempts and vary a
  bounded array/worker throttle instead of encoding current free-node count
  into a static DAG.
- At 200 m, the validated four-node layout permits at most eleven concurrent
  chains on 44 available GPU nodes. At 100 m, the provisional 16-node layout
  permits two and leaves 12 nodes unusable for another unmodified 100 m job.
  Parallelize only across scientifically qualified independent chains; a
  single restart chain remains sequential.
- Current Balfrin guidance limits sustained uncoordinated `normal` use to 50%
  of the machine and limits `pp-short` to two jobs per user. Use a global
  producer throttle rather than multiplying producer workers by active model
  chains. Treat `normal` as the coordinated/stable tier, `lowprio` as queued
  background overflow, and `preemptible` as cancellation-tolerant
  opportunistic burst capacity.

## Canonical artifacts

- 250 m regression: `case_studies/icon_ch1_eps_20260710T18_alps_250m`
- Swiss 100 m planning/input case: `case_studies/swiss_100m`
- Swiss 200 m active case: `case_studies/swiss_200m`
- Swiss 200 m six-hour qualification:
  `case_studies/swiss_200m/validation/production_6h_qualification_4927314.json`
- REA-L 20-year resource estimate:
  `case_studies/swiss_200m/validation/rea_l_20year_resource_estimate.json`
- Streaming production contract:
  `case_studies/swiss_200m/streaming/README.md`
- Long-duration scientific qualification contract:
  `case_studies/swiss_200m/config/scientific_pilot_plan.json`
- Radiation cloud-fraction recovery qualification:
  `case_studies/swiss_200m/validation/radiation_cloud_fraction_recovery_qualification.json`
- Restart-initialization national v2 qualification (`FAIL`, deliberately no
  ready marker):
  `case_studies/swiss_200m/validation/restart_continuity/restart_initialization_qualification.json`
- Restart-initialization national v1 diagnostic:
  `case_studies/swiss_200m/validation/restart_continuity/restart_initialization_v1_diagnostic.json`
- Annual-to-20-year production assessor:
  `case_studies/swiss_200m/validation/assess_scientific_annual.py`
- Independent-validation dataset and metric contract:
  `case_studies/swiss_200m/config/observational_validation_contract.json`
- REA-L land-state initialization:
  `case_studies/swiss_200m/validation/rea_l_land_initialization_20200701_0000.json`
- Winter REA-L land-state initialization:
  `case_studies/swiss_200m/validation/rea_l_land_initialization_20200115_0000.json`
- REA-L land-state HICAR consumption smoke:
  `case_studies/swiss_200m/validation/rea_l_land_initialization_smoke_20200701_00_02.json`
- Surface-class interpretation of qualification extremes:
  `case_studies/swiss_200m/validation/land_init_smoke_surface_classes.json`
- Class-aware smoke budgets and trends:
  `case_studies/swiss_200m/validation/land_init_smoke_scientific_diagnostics.json`
- Three-hourly REA-L event reference publications:
  `case_studies/swiss_200m/validation/rea_l_surface_reference_summer_20200701_20200704.json`
  and
  `case_studies/swiss_200m/validation/rea_l_surface_reference_winter_20200115_20200118.json`
- Hourly internal SwissMetNet event observation manifests:
  `case_studies/swiss_200m/validation/swissmetnet_observations_summer_20200701_20200704.json`
  and
  `case_studies/swiss_200m/validation/swissmetnet_observations_winter_20200115_20200118.json`
- Public MeteoSwiss OGD event-grid manifest:
  `case_studies/swiss_200m/validation/ogd_grid_reference_2020_events.json`
- Wind-climatology product contract:
  `case_studies/swiss_200m/wind_climatology/PRODUCT_CONTRACT.md`
- Wind-climatology 701 x 701 bridge qualification:
  `case_studies/swiss_200m/wind_climatology/validation/bridge_wind_surface_pbl_20100101.json`
- Wind-climatology Swiss national restart/archive qualification:
  `case_studies/swiss_200m/wind_climatology/validation/national_wind_stream_20100101.json`
- Durable procedures: `.agents/skills/`
