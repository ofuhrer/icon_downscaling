# hicarprep

`hicarprep` is the only maintained ICON-to-HICAR preprocessor in this project.
It decodes native REA-L fields, maps them directly to the HICAR grid, performs
terrain-aware vertical reconstruction, converts all water species jointly to
dry-air mixing ratios, and writes the regular HICAR forcing record. It can also
write a sparse lateral-boundary frame from the same transformed state for an
explicit sparse-relaxation experiment.

Atmospheric fields are P, T, U, V, QV, QC, QI, HHL and W from native REA-L.
Operational REA-L lacks QI, so `source-absent-zero` is an explicit source-data
policy. W is terrain-adjusted, interpolated to the authoritative target HFL
mass levels, and supplied through regular forcing for HICAR's variational wind
projection. W is never inserted through sparse LBC.

Typical hourly preparation:

```bash
python scripts/hicarprep.py decode-icon-atmosphere \
  --dynamic-grib dynamic.grib --geometry-grib geometry.grib \
  --icon-extpar icon_extpar.nc --valid-time 2020-02-10T00:00:00Z \
  --missing-qi-policy source-absent-zero --output native.nc

python scripts/hicarprep.py prepare-hicar-forcing \
  --icon-state native.nc --static runtime_domain.nc \
  --weights rbf.nc --vector-weights vector_rbf.nc --target-sst target_sst.nc \
  --output forcing.nc --column-workers 8 --rbf-backend numba
```

The national Swiss-domain throughput path uses the pinned Numba dependency for
scalar RBF application; `--rbf-backend numpy` remains the exact-arithmetic
fallback. Add `--boundary forcing.lbc.nc` only for a sparse-LBC experiment.
Recurring regular records use lossless deflate level 1; sparse frames store
the atmospheric and geometry fields as float32 with level-1 deflate in bounded
point chunks. This matches the precision seen by HICAR's single-precision
readers while avoiding the former float64/level-4 encoding cost.

`target_sst.nc` is produced for the same exact valid time from REA-L `SKT`.
Target water with compact same-surface support uses that remap. Target water
without it uses the exact-time monotone local all-surface RBF baseline; a mask
and the nearest same-surface candidate distance are retained as diagnostics,
but no remote candidate value is inserted. The static-domain checksum, grid
fingerprint, water mask, and valid time must all match the atmospheric record.
HICAR reads this field as `SST` from every hourly regular record.

The regular record initializes HICAR, advances its forcing clock and is the
selected literature-established lateral-relaxation path with
`relax_filters=true`. Required checks are schema, finite/plausible values,
exact target grid and continuous ordered times. Sparse experiments additionally
require fixed LBC geometry/schema and matching ordered timestamps.

Land initialization uses native REA-L soil/snow fields:

```bash
python scripts/hicarprep.py prepare-surface \
  --icon-surface native_surface.nc --static static.nc --weights rbf.nc \
  --noahmp-table NoahmpTable.TBL --soil-water-method smi \
  --output surface.nc
python scripts/hicarprep.py assemble-runtime-domain \
  --static static.nc --surface surface.nc --output runtime_domain.nc
```

The selected land method is SMI transfer with four depth-varying Noah-MP soil
textures. Alternative transfer methods remain callable for controlled A/B
experiments, but they are not separate publication paths.
