# Switzerland 200 m R&D case

The selected baseline is explicit in `config/hicar_swiss_200m.nml.in`:

- 80 levels, 15 m lowest layer, 12 km top, SLEVE 2/6;
- native hicarprep P/T/U/V/QV/QC/QI forcing in dry-air mixing ratios;
- W diagnosed by HICAR and sparse target-grid lateral relaxation;
- variational wind, Sx on, density advection, `alpha_const=1`;
- Noah-MP with SMI initialization and four depth-varying soil textures;
- terrain radiation off while its cadence/restart behavior is being tested.

The atmospheric forcing file keeps mass-grid U/V for HICAR's normal
initialization interpolation. The paired sparse LBC explicitly carries U and V
on separate `(nx+1, ny)` and `(nx, ny+1)` face supports; both products are
validated before their ready markers are created.

The maintained scripts build HICAR, prepare atmospheric and land input, render
the namelist, run one restartable segment, retrieve SwissMetNet data, and
compare HICAR with stations and REA-L. Experimental alternatives should be
small explicit diffs from this baseline, not additional workflow profiles.
