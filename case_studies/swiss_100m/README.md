# Switzerland-wide 100 m domain

This case defines one continuous HICAR domain: a 454 x 330 km, 100 m grid
centred at 46.815 N, 8.225 E (4,541 x 3,301 points; 14,989,841 horizontal
cells). It conservatively encloses Switzerland's geographic bounding box with
at least 50 km projected margin. Swiss territory is therefore not itself a
lateral boundary zone and has a residual high-resolution buffer.

The outer 30 km transitions from the high-resolution DEM to REA-L `HSURF`.
The 50 km exterior margin leaves at least 20 km of fully high-resolution
topography between Switzerland and that transition. The
structured ICON forcing grid must cover the full HICAR domain plus another
10 km. These values are production candidates and require 40/50/60 km
boundary-sensitivity validation before data are published.

`validation/planned_fieldextra_grid.py` emits the coarse structured forcing
grid directly from this planned geometry. It is intentionally independent of a
generated static file, so REA-L `HSURF` can be regridded before the final static
domain is built. It uses only the Python standard library so it also runs in
Balfrin's Python 3.6 environment; its spherical-edge calculation rounds
outward to the conservative grid 45.20--48.39 N, 5.02--11.43 E.

## Build order

1. Submit `scripts/regrid_rea_l_hsurf.sbatch` for a representative source
   timestamp. It produces the required `HSURF`, `lat_1`, and `lon_1` boundary
   file on the conservative grid above, and validates its schema before
   publication. The source bundles can be created from a selected timestamp
   using `scripts/extract_rea_l_source.sbatch`; it extracts 80 full levels
   (`P,T,U,V,QV`), 81 half levels (`W,HHL`), and `HSURF,FR_LAND` with message
   counts and atomic ready-marker publication.

   REA-L `reanl` records are organized as a 00 UTC cycle with hourly
   `step=0..24` fields. For operational forcing, set `REA_STEP` explicitly
   when calling `extract_rea_l_source.sbatch` and
   `regrid_rea_l_forcing.sbatch`; valid time is cycle time plus that step.
   The static geometry is retrieved at step 0 while atmospheric fields and
   vertical velocity use the requested step. The production namelist therefore
   uses a 3600 s forcing interval.
2. Set `BOUNDARY_TOPO` and inspect the static-generation command:

   ```bash
   case_studies/swiss_100m/scripts/prepare_static_domain.sh
   ```

3. Run the same command with `--execute` on a machine with the public-data
   dependencies and sufficient scratch space.  It validates the static domain,
   derives the coarse forcing grid, and only then writes the static `.ready`
   marker.
4. Run short 100 m capacity and physical-quality cases before a national run.

After the static and forcing files have ready markers, render the model input
with `scripts/render_hicar_namelist.py`. The renderer accepts the same
published streaming plan and restart contract as the 200 m case. The template
fixes the tested 80-level variational-wind/SLEVE 2/6 configuration and
production physics, including the layered soil-temperature and soil-water
inputs. The routine profile contains the common eleven hourly two-dimensional
diagnostics; use the engineering profile only for short validation cases.

The actual national static grid independently passes the frozen 80-level
SLEVE margins. Balfrin job `4931258` reproduced the configured 5-cell,
10-cycle large/small-scale split and found a minimum mass Jacobian of
0.260878, minimum interface thickness of 12.566 m, and minimum mass-level
spacing of 13.422 m. The published evidence is
`validation/sleve_geometry_80l.json`.

The next allocation is governed by
`config/engineering_capacity_gate.json`, not the older raw
`swiss_100m_gpu_capacity.sbatch` template. The gate can only be planned after
the paired 200 m event assessment publishes the exact
`GO_MONTH_AND_100M_CAPACITY_GATE` decision. Run
`scripts/prepare_engineering_capacity_gate_balfrin.sbatch` to publish two
two-hour plans spanning 00--02 and 02--04 UTC. Their boundary forcing record
is shared, the first segment writes a full exact-end restart, and the second
must explicitly reread it.

`streaming/submit_engineering_capacity_gate.py` is dry-run by default. Its
ten-job DAG produces and validates five unique forcing records, runs both
16-node segments, audits every physical wind solve, compares all routine
fields between history and the restart at 02 UTC, and publishes a final
capacity verdict. Every compute GPU and node is sampled once per second.
Acceptance requires at least 15% device and host-memory headroom everywhere,
the frozen SLEVE and conservation margins, exact unique 00--04 output times,
successful restart continuation, and measured forcing/model/restart/output/
validation costs within the allocation envelope. Both model completions must
also pass the production-provenance contract that binds the clean source
commit, executable, static domain, forcing publication, chunk plan, and model
log by SHA-256, and both segments must share one source/executable/static
identity. The config deliberately does not pin a historical commit. Planning
requires `HICAR_MONTH_SOURCE_QUALIFICATION` to name the published, passing
source qualification selected for the 200 m month; the plan freezes its
commit, mode, parent, path, and checksum. The plan, submitter, runner, and
final assessor revalidate that identity, so an older clean checkout or a
capacity result from another HICAR baseline cannot enter scientific scaling.
The submitter also checks the provenance semantics of the 100 m runner, shared
model validator, and capacity assessor, then records checksums for the entire
referenced Slurm stack plus those critical Python files in its preview and
receipt. A stale critical Balfrin copy fails before any 16-node job is
submitted.

A passing verdict qualifies only national 100 m engineering capacity. It does
not validate 100 m physics, scientific added value, land/snow spin-up,
long-duration drift, or a production campaign. The raw
`scripts/swiss_100m_gpu_capacity.sbatch` remains only historical scaffolding
for the July attempts that stopped before model initialization because of
launcher/forcing-list defects.

The next gate is frozen separately in
`config/scientific_scaling_gate.json`. It cannot start until both the 200 m
month publishes `GO_ANNUAL_CYCLE` and the capacity gate publishes
`QUALIFIED_100M_ENGINEERING_CAPACITY_ONLY`. It repeats the 72-hour winter and summer
events at 100 m and 200 m with identical forcing and observation samples,
requires production water/energy and hour-48 restart-continuity gates, and
compares the two resolutions with paired 24-hour block-bootstrap uncertainty.
At least two terrain-sensitive metric families must improve in median, one
must have a positive 95% lower confidence bound, and no family may degrade by
more than five percent. A pass authorizes only a 31-day 100 m month pilot; an
annual or 20-year 100 m campaign remains forbidden. Validate the contract
before planning with:

```bash
python case_studies/swiss_100m/validation/validate_scientific_scaling_gate.py \
  case_studies/swiss_100m/config/scientific_scaling_gate.json
```

The case is deliberately not runnable until the REA-L source-data access and
coverage gates are satisfied; it must never substitute placeholder terrain or
unblended boundary topography.

`config/resource_plan.json` retains the original capacity calculation for a
64-A100 launch (16 nodes, four compute ranks and one CPU-only I/O rank per
node). The current campaign-scale source of truth is
`swiss_200m/validation/rea_l_20year_resource_estimate.json`, which accounts
for the eleven-field routine profile and both resolutions. Hourly
three-dimensional output remains a validation-only product.
