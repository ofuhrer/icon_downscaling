# Alpine sensitivity evidence

| Question | Evidence | Decision |
| --- | --- | --- |
| Upper-level W | A 31-level test showed large top-level sensitivity to terrain treatment and relaxation. | Never validate W from one surface level; inspect vertical profiles and terrain classes. |
| Vertical grid | The selected 80-level/15 km SLEVE grid has 17.008 m minimum spacing and 0.3375 minimum Jacobian. Older aggressive splits inverted locally; order-8 external DEM filtering did not repair them. | Keep the selected grid; filtering is a separate physical sensitivity, not a geometry cure. |
| Wind alpha | With executable `4a425677` and all other inputs fixed, diagnosed alpha reached 139.30 m s-1 at 10 m; `alpha_const=1` reached 21.085 m s-1. Both solves converged. | Use alpha 1 for the bounded reference; revisit dynamic alpha only as a mechanism experiment. |
| RK3 thermodynamics | Current theta-reference hardening removed NaNs in the one-hour smoke, while earlier unbounded flux forms produced severe upper-level outliers. | Retain upper-level T/theta extrema and 6--24 h stability checks; smoke success is not production proof. |

If a new wind-mechanism hypothesis is justified, vary one factor in this order:
wind iterations/dynamic-alpha formulation, numerical diffusion, density
advection/wind balance, AGL transition, then boundary width/interpolation. Do
not revive the rejected `decay_rate_L_topo=1`, `decay_rate_S_topo=1` setup.
