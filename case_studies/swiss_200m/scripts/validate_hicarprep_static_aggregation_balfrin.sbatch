#!/bin/bash -l
# Regenerate the representative 200 m bridge domain with field-specific aggregation.
#SBATCH --job-name=hprep-static
#SBATCH --partition=pp-short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=01:00:00

set -euo pipefail
repo_root=${REPO_ROOT:-$SCRATCH/icon_hicar}
. "$repo_root/scripts/load_balfrin_site_config.sh"
[ -f /etc/profile.d/modules.sh ] && . /etc/profile.d/modules.sh
module use "$USER_ENV_ROOT/modules"
module load python/3.11.7 >/dev/null

python_bin=${STATIC_PYTHON:-$SCRATCH/icon_hicar/qualification/static_abc_v1/python-rasterio-v1/bin/python}
baseline=${BASELINE_STATIC:-$SCRATCH/icon_hicar/case_studies/alpine_bridge_200m/static/domain_static_alpine_bridge_200m.nc}
cache=${STATIC_SOURCE_CACHE:-$SCRATCH/icon_hicar/cache/hicar_static_public}
output_root=${STATIC_VALIDATION_ROOT:-$SCRATCH/icon_hicar/qualification/hicarprep_static_fractional_v1}
output=$output_root/domain_static_alpine_bridge_200m_fractional_v1.nc

for path in "$python_bin" "$baseline" "$baseline.ready" "$repo_root/scripts/prepare_static_inputs.py"; do
  test -e "$path" || { echo "missing required path: $path" >&2; exit 2; }
done
test ! -e "$output" && test ! -e "$output.ready" || {
  echo "refusing to overwrite static validation product: $output" >&2
  exit 2
}
mkdir -p "$output_root"
commit=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || printf unspecified)
"$python_bin" "$repo_root/scripts/prepare_static_inputs.py" \
  --output "$output" \
  --center-lat 46.5593529 --center-lon 8.4232283 \
  --width-km 140 --height-km 140 --dx-m 200 \
  --public-sources --static-field-set land-surface --cache-dir "$cache" --offline \
  --preserve-topography-from "$baseline" --lu-categories USGS \
  --generating-commit "$commit"

"$python_bin" - "$baseline" "$output" "$output_root/validation.json" <<'PY'
import hashlib
import json
import sys
import netCDF4
import numpy as np

baseline_path, candidate_path, report_path = sys.argv[1:]
with netCDF4.Dataset(baseline_path) as baseline, netCDF4.Dataset(candidate_path) as candidate:
    np.testing.assert_array_equal(candidate["topo"][:], baseline["topo"][:])
    fractions = np.asarray(candidate["landuse_fraction"][:], dtype=np.float64)
    dominant = np.asarray(candidate["landuse"][:], dtype=np.int64)
    soil_layers = np.asarray(candidate["soil_type_layer"][:], dtype=np.int64)
    surface_soil = np.asarray(candidate["soil_type"][:], dtype=np.int64)
    if soil_layers.shape != (4, *dominant.shape):
        raise SystemExit(f"soil_type_layer has unexpected shape {soil_layers.shape}")
    if np.any((soil_layers < 1) | (soil_layers > 13)):
        raise SystemExit("soil_type_layer lies outside Noah-MP STAS classes 1..13")
    if not np.array_equal(surface_soil, soil_layers[0]):
        raise SystemExit("soil_type is not identical to the uppermost soil_type_layer")
    layer_difference_counts = [
        int(np.sum(soil_layers[layer] != soil_layers[0])) for layer in range(4)
    ]
    texture_sum = sum(
        np.asarray(candidate[name][:], dtype=np.float64)
        for name in ("soil_sand_percent", "soil_silt_percent", "soil_clay_percent")
    )
    report = {
        "status": "PASS_STATIC_AGGREGATION_SCHEMA",
        "candidate": candidate_path,
        "candidate_sha256": hashlib.sha256(open(candidate_path, "rb").read()).hexdigest(),
        "grid_shape": list(dominant.shape),
        "fraction_closure_max_abs_error": float(np.max(np.abs(fractions.sum(axis=0) - 1.0))),
        "mixed_category_cell_count": int(np.sum(np.sum(fractions > 1.0e-6, axis=0) > 1)),
        "dominant_category_mismatch_count": int(
            np.sum(np.argmax(fractions, axis=0) + 1 != dominant)
        ),
        "glacier_fraction_cell_count": int(np.sum(fractions[23] > 0.0)),
        "urban_fraction_cell_count": int(np.sum(fractions[0] > 0.0)),
        "soil_type_layer_shape": list(soil_layers.shape),
        "soil_layer_difference_cell_counts": layer_difference_counts,
        "depth_varying_soil_cell_count": int(
            np.sum(np.any(soil_layers[1:] != soil_layers[0], axis=0))
        ),
        "soil_texture_sum_percent_minimum": float(np.min(texture_sum)),
        "soil_texture_sum_percent_maximum": float(np.max(texture_sum)),
        "topography_bitwise_preserved": True,
    }
if report["fraction_closure_max_abs_error"] > 1.0e-5:
    raise SystemExit("land-use fractions do not close")
if report["dominant_category_mismatch_count"]:
    raise SystemExit("dominant land-use category disagrees with fractions")
if report["depth_varying_soil_cell_count"] == 0:
    raise SystemExit("full static candidate contains no depth-varying soil texture")
if not (
    report["soil_texture_sum_percent_minimum"] >= 99.5
    and report["soil_texture_sum_percent_maximum"] <= 100.5
):
    raise SystemExit("SoilGrids sand/silt/clay fractions do not close near 100 percent")
with open(report_path, "w") as stream:
    json.dump(report, stream, indent=2, sort_keys=True)
    stream.write("\n")
print(json.dumps(report, sort_keys=True))
PY
touch "$output_root/validation.json.ready"
echo "PASS: static aggregation validation $output"
