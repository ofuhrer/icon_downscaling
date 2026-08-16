# Switzerland 200 m R&D case

`REFERENCE_SETUP.md` is the scientific specification and
`config/hicar_swiss_200m.nml.in` is the executable namelist template. The
evaluated configuration uses 80 levels to 15 km, native hourly hicarprep
forcing on exact HHL/HFL, regular full-domain relaxation, fixed alpha 1,
Sx/TPI off, Noah-MP, and terrain-corrected 600 s radiation.

Two four-season campaigns found neither Sx setting consistently better than
native REA-L/interpolation-only. Sx-off reduces common-regime over-damping but
loses the Sx-on autumn vector advantage. The completed campaign used CPU RRTMG
and was only numerically restart-reproducible; production `0b9b0cb6` also
qualifies GPU RTE-RRTMGP v1.9.3 with bitwise one-node and 12-node restart gates.

Maintained scripts build HICAR, prepare atmospheric/land input, render the
namelist, run restartable segments, retrieve SwissMetNet data, and compare
HICAR with stations and REA-L. New experiments should be small one-factor diffs
from the reference and must recreate their runtime/inputs because Balfrin
scratch is intentionally empty.
