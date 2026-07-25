# Swiss 200 m terrain-filter sensitivities

These files are controlled solver sensitivities derived from
`../static/domain_static_swiss_200m.nc`. The unfiltered static file remains the
scientific reference.

`domain_static_swiss_200m_shapiro8_p1.nc` is the primary narrow-band case. It
uses one order-8 Shapiro pass. `domain_static_swiss_200m_shapiro8_p2.nc` repeats
that pass once as a stronger bounded case. The order-4 file is a broader
diagnostic and is not the preferred first comparison because it attenuates
about 12% of a five-cell (1 km) wave.

The external tool filters `topo_highres` without mixing land and water, keeps
water elevations unchanged, then recomputes `topo` from the existing
`topo_driving` and `topo_blend_weight`. Coordinates, land use, soil, and
surface initialization fields are copied unchanged. Each manifest records
source/output checksums, the nominal transfer function, point changes, and
1/5/10 km block-mean changes.

Recreate a case with:

```bash
python3 scripts/filter_static_topography.py \
  --input case_studies/swiss_200m/static/domain_static_swiss_200m.nc \
  --output <new-static.nc> --order 8 --passes 1 --report <manifest.json>
```

Do not publish HICAR physical output from a sensitivity until the wind solver
passes its true-residual acceptance gate.
