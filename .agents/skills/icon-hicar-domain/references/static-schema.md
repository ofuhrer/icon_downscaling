# Static-domain reference

## Core fields

The exact static schema depends on enabled HICAR physics, but Alpine NoahMP runs need at least:

- high-resolution terrain/topography;
- projected `x,y` and geographic latitude/longitude;
- land-use category and land/water information;
- soil type and initialized soil state for the configured layers;
- any initial atmospheric/static variables required by the selected namelist.

Use `scripts/prepare_static_inputs.py --help` and the frozen case file as the executable schema authority.

## Source policy

- Public reproducible default: Copernicus DEM GLO-30, ESA WorldCover 2021, SoilGrids.
- Use cache reuse; do not repeatedly download global tiles.
- Prefer project- or operational-quality national datasets when licensing and reproducibility permit, but record provenance and transformations.
- Use ICON `HSURF` only for boundary compatibility, not as the high-resolution interior DEM.

## Scaling

Approximate horizontal cell counts before halos:

```text
cells ~= (width / dx + 1) * (height / dy + 1)
```

Switzerland-scale coverage at 100 m is tens of millions of horizontal cells and can be impractical without decomposition, storage planning, and staged tests. Prove the workflow at 250 m and on a smaller subdomain before scaling area or resolution.

## Terrain filtering

For an optional, reproducible sensitivity:

```bash
python3 scripts/filter_static_topography.py \
  --input "$UNFILTERED_STATIC" \
  --output "$FILTERED_STATIC" \
  --passes 1 \
  --order 8 \
  --strength 1 \
  --water-policy preserve \
  --report "$FILTER_MANIFEST"
```

The order-8 one-pass nominal amplitude response is approximately 0, 0.684,
0.938, and 0.986 at wavelengths of 2, 3, 4, and 5 grid cells, respectively,
and is essentially unity beyond 10 cells. The tool filters only land terrain,
reapplies the existing driving-terrain boundary blend, validates the complete
static file, publishes atomically, and writes a checksum-bearing ready marker.
Always start from the unfiltered source; never stack filtered files.

## Frozen reference case

```text
Local:   case_studies/icon_ch1_eps_20260710T18_alps_250m
Balfrin: $SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m
```

Reference geometry: 20 km x 20 km, center 46.75 N / 8.15 E, 250 m spacing, 81 x 81 static grid. Use its `static/domain_static_relaxed.nc` and validation report to compare schema and boundary blending only.
