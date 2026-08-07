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
- Aggregate categorical land cover with the modal source class at target resolution before reclassification.
- Map all six SoilGrids depth means to the four Noah-MP layers by thickness overlap; retain per-layer classes, composition, and bounds.
- Use cache reuse; do not repeatedly download global tiles.
- In a land/soil-only A/B/C audit, preserve the published baseline terrain
  fields bitwise with `--preserve-topography-from` and require a zero-tolerance
  terrain comparison. Represent B/C as one static file plus distinct
  `nmp_opt_soil` configurations, not as duplicated static products.
- Use swissALTI3D, swissTLM3D, and epoch-aware GLAMOS glacier outlines first as Swiss audit sources. Promote them as replacements only after crosswalk, border-seam, and paired-response validation; record provenance and transformations.
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

## Terrain-radiation geometry

Treat horizon/SVF fields as a separately audited physics input, not an
automatic consequence of having a high-resolution DEM. HICAR's current
runtime contract is:

- `hlm(azimuth,y,x)` has exactly 90 sectors and stores zenith angle to the
  horizon in degrees; flat/open terrain is 90 degrees;
- `azimuth` is exactly `0,4,...,356` degrees clockwise from north;
- `svf(y,x)` is finite in [0,1];
- `slope_angle` and `aspect_angle` are in radians and derive from the same
  terrain realization as HLM/SVF;
- the geometry records generator/version, source-DEM SHA-256, vertical datum,
  positive search distance, and horizon convention.

Use
`case_studies/swiss_200m/fixed_parameters/publish_terrain_radiation_static.py`
to merge validated geometry into a published base static file. It copies HLM
one azimuth slab at a time, writes a checksum-bound manifest, atomically
renames, and publishes ready markers last. Never enable a terrain-radiation
profile against an ad hoc static file; the national renderer requires the
publisher's audited global attributes.

## Frozen reference case

```text
Local:   case_studies/icon_ch1_eps_20260710T18_alps_250m
Balfrin: $SCRATCH/icon_hicar/case_studies/icon_ch1_eps_20260710T18_alps_250m
```

Reference geometry: 20 km x 20 km, center 46.75 N / 8.15 E, 250 m spacing, 81 x 81 static grid. Use its `static/domain_static_relaxed.nc` and validation report to compare schema and boundary blending only.
