# Architecture

The coordinator has four scientific stages:

```text
REA-L/FDB -> hicarprep forcing + land state -> HICAR segments -> comparisons
```

HICAR owns model initialization, pressure adjustment, wind projection,
physics, and restart serialization. `hicarprep` owns native-field decoding,
horizontal/vertical transformation, water conversion, target forcing, and
sparse LBC extraction. The case directory owns one selected namelist. The
campaign controller owns only bounded submission and restart chaining.

NetCDF schema, finite/range, exact grid, chronological input, successful model
termination, and terminal restart checks remain because failures would change
scientific interpretation. Promotion states, certification layers, duplicate
manifests, archive gates, and production lifecycle machinery are intentionally
absent during R&D.
