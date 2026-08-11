# Terrain-radiation geometry

The selected 200 m Swiss domain uses HORAYZON v1.2.1 to compute a 90-sector
topographic horizon and slope-aware sky-view factor.  The source mesh is the
exact final HICAR `topo` on the 2061 x 1431 target, surrounded by 101 cells
(20.2 km) of REA-L-CH1 driving terrain.  The extra cell gives a complete mesh
cell at the 20 km ray limit.  The driving source must cover that complete
outer band and agree with the base-static edge within 1 m.

Copernicus GLO-30 is delivered as EGM2008 orthometric height.  HORAYZON's
curved-Earth algorithm requires ellipsoidal height, so the generator requires
PROJ's official 2.5 arc-minute `us_nga_egm08_25.tif` grid and verifies its
SHA-256 before applying EPSG:9518 to EPSG:4979.  REA-L `HSURF` is a model
mean-sea-level terrain height and is explicitly treated as orthometric in the
outer relaxation band; this approximation does not reach the Swiss analysis
region because the HICAR border is wider than the 20 km horizon search.

The resulting static fields are:

- `hlm(azimuth,y,x)`: zenith angle to the horizon in degrees, with 90 degrees
  denoting an unobstructed horizon;
- `azimuth`: exactly 0, 4, ..., 356 degrees clockwise from north;
- `svf(y,x)`: isotropic sky-view factor for the local slope;
- `slope_rad(y,x)` and `aspect_rad(y,x)`: radians, with aspect clockwise from
  north.

The environment is pinned in `config/terrain_radiation_environment.yml`.  A
single exclusive `pp-long` node is sufficient; the shared-memory ray tracer
does not benefit from multiple Balfrin nodes.  The batch script requests 128
CPU cores, 220 GB, and four hours.  Expected HLM storage is 1.06 GB before
compression; the final static file is expected to be about 6 GB.

Scientific sources are the [Copernicus DEM Product Handbook](https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf)
and [Steger et al. (2022)](https://doi.org/10.5194/gmd-15-6817-2022).
