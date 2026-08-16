# Wind-climatology output contract

The `wind_climatology` profile is the long-run production profile for a later
Swiss wind climatology. It writes one ending-hour record every 3600 seconds.
The model weights every diagnosed state by its exact adaptive timestep; it
does not approximate the hour with six instantaneous snapshots.

## Retained fields

| NetCDF field | Levels | Meaning |
| --- | --- | --- |
| `u_agl_mean_1h`, `v_agl_mean_1h` | 50, 75, 100, 125, 150, 200, 250 m AGL | hourly vector-mean grid-relative components |
| `wind_speed_agl_mean_1h` | same | hourly scalar mean of `sqrt(u^2+v^2)` |
| `wind_speed_agl_10min_max_1h` | same | maximum of the six timestep-weighted ten-minute scalar means |
| `u10m_mean_1h`, `v10m_mean_1h` | 10 m AGL | hourly vector-mean grid-relative components for SwissMetNet verification |
| `wind_speed_10m_mean_1h` | 10 m AGL | hourly scalar mean for SwissMetNet `fkl010h0` verification |
| `wind_speed_10m_10min_max_1h` | 10 m AGL | maximum of the six ten-minute means |
| `psfc`, `taix`, `hus2m`, `ustar`, `sfc_Ri`, `hpbl` | surface | inexpensive hourly endpoint health/context diagnostics |

The maximum-of-ten-minute-means fields are not instantaneous gusts and must
not be labelled as gusts. HICAR does not currently have a validated gust
diagnostic. Static fields, including terrain, coordinates, land mask, and
surface roughness, remain in the static domain rather than being repeated at
every hour. The `station` profile remains available for short, targeted runs
that need all six ten-minute surface records.

The fixed-height wind is interpolated linearly in geometric height without
extrapolation. A cell/height is missing when the model column does not bracket
the requested height. Components are grid-relative; downstream products must
rotate the vector mean to Earth coordinates before deriving direction.

## SwissMetNet verification

`validation/compare_wind_climatology_to_smn.py` compares the retained 10 m
hourly scalar speed and vector-mean direction with quality-controlled
SwissMetNet `fkl010h0` and `dkl010h0`. It uses the same bounded nearest-land-cell
mapping and HICAR grid rotation as the event evaluator. Direction is scored
only for observed speeds of at least 2.5 m s-1. The ten-minute maximum is
reported as an unpaired model diagnostic because the current observation
extract has no like-for-like gust quantity.

## Starts and restarts

The cold-start timestamp is missing for all averaged wind fields because it
has no preceding hour. A complete value first appears one hour after the
integration start. The profile is accepted only with a 3600-second output
interval, and campaign segment endpoints must fall on whole UTC hours.
HICAR checkpoints are written at output events (`restartinterval=1` in the
campaign wrapper), so a restart begins with empty accumulators immediately
after a completed hourly record. No partial averaging interval crosses a
restart.

For long campaigns, keep the latest checksum-validated terminal restart that
is still needed by each active chain. Move selected monthly/seasonal recovery
checkpoints to durable storage only after validation; intermediate restarts are
scratch data and may be removed only after the successor segment has completed
and the usual live-job/evidence audit has passed.

## Storage model

The profile contains 38 two-dimensional float32 plane-equivalents per hour:
28 fixed-height wind planes, four 10 m wind planes, and six health planes.
Ignoring small coordinate/time overhead, its raw size is

`grid_cells * 38 * 4 bytes * 24 * 365.25 * years`.

For the approximately 2.95-million-cell full Swiss production domain this is
about 39.3 TB for 10 years or 78.6 TB for 20 years (decimal TB). A Swiss-only
2.45-million-cell crop would be about 32.6/65.3 TB. An illustrative 2.2:1
lossless-compression assumption would reduce those figures to roughly
17.9/35.7 TB and 14.8/29.7 TB, respectively; this is not a capacity estimate.
Compression must be measured on representative closed production output
before it is used for capacity commitments. A short integration smoke test is
also not representative because its cold-start fill record compresses almost
completely.

The former 47-plane evaluation profile at ten-minute cadence corresponds to
282 plane-equivalents per hour, about 583 TB raw over 20 years on the full
domain. The new profile is therefore about 7.4 times smaller before any
compression. These estimates exclude restart retention, filesystem metadata,
and temporary copies made during validation or compaction.

For a closed, validated segment file,
`validation/compact_wind_climatology_output.py INPUT OUTPUT` creates a
shuffle+deflate-level-1 copy under a temporary name, verifies every variable
and mask bit-for-bit, atomically publishes the requested output, and records
sizes plus SHA-256 digests. It never replaces or deletes the input. Measure the
reported ratio and read/write cost on representative segment files before
making compaction part of the production chain; only then may the uncompressed
copy be considered for audited removal.

The two-hour Gaudergrat integration gate reduced 10,927,006 bytes to
3,795,682 bytes (2.88:1) and preserved every variable and mask. This confirms
the compaction workflow, but it is not a production capacity measurement: one
of only three records is the highly compressible all-fill cold start. Exact
build, continuous/restart, and checksum evidence is recorded in
`validation/wind_climatology_output_v1.json`.
