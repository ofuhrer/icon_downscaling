#!/usr/bin/env python3
"""Add post-hoc robustness context to a frozen land-response decision.

This diagnostic never changes the frozen viability thresholds or decision.  It
quantifies how many states crossed the frozen DRYSMC tolerance, by how much,
and where, so a sparse threshold crossing is not confused with broad hydraulic
failure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from collections import Counter
from pathlib import Path

import netCDF4
import numpy as np


SOIL_NAMES = {
    1: "sand",
    2: "loamy_sand",
    3: "sandy_loam",
    4: "silt_loam",
    5: "silt",
    6: "loam",
    7: "sandy_clay_loam",
    8: "silty_clay_loam",
    9: "clay_loam",
    10: "sandy_clay",
    11: "silty_clay",
    12: "clay",
    13: "organic_material",
    14: "water",
    15: "bedrock",
    16: "land_ice",
    17: "playa",
    18: "lava",
    19: "white_sand",
}


def assessor_module(path: Path):
    spec = importlib.util.spec_from_file_location("land_response_assessor", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load assessor {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def counts(values: np.ndarray) -> dict[str, int]:
    return {str(int(key)): int(value) for key, value in sorted(Counter(values).items())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--assessor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    assessment = json.loads(args.assessment.read_text())
    definition = json.loads(args.definition.read_text())
    assessor = assessor_module(args.assessor)
    tolerance = float(definition["viability_thresholds"]["hydraulic_tolerance_m3_m3"])
    methods = tuple(assessment["arms"])

    static_path = Path(assessment["provenance"]["smi"]["runtime_domain"])
    with netCDF4.Dataset(static_path) as static:
        land = np.asarray(static["landmask"][:]) >= 0.5
        landuse = np.asarray(static["landuse"][:], dtype=np.int64)
        active = land & (landuse != 24)
        soil_type = np.asarray(static["soil_type_layer"][:], dtype=np.int64)
        latitude = np.asarray(static["lat"][:], dtype=np.float64)
        longitude = np.asarray(static["lon"][:], dtype=np.float64)

    hydraulics = assessor.parse_noahmp_stas_hydraulics(
        Path(assessment["noahmp_table"]["path"])
    )
    lookup = np.clip(soil_type - 1, 0, 18)
    dry = hydraulics["DRYSMC"][lookup]
    active3 = np.broadcast_to(active, soil_type.shape)
    active_state_count = int(np.count_nonzero(active3))
    active_column_count = int(np.count_nonzero(active))
    run_records = {
        method: assessor.records(Path(assessment["provenance"][method]["run"]))
        for method in methods
    }
    total = {
        method: {
            timestamp: assessor.read_record(path, index, "soil_water_content")
            for timestamp, (path, index) in records.items()
        }
        for method, records in run_records.items()
    }

    time_series = {}
    for method in methods:
        method_series = []
        for timestamp in sorted(total[method]):
            margin = total[method][timestamp] - dry
            violation = (margin < -tolerance) & active3
            indices = np.argwhere(violation)
            method_series.append({
                "valid_time": timestamp.isoformat(),
                "count_below_frozen_tolerance": int(indices.shape[0]),
                "fraction_of_active_soil_states": float(indices.shape[0] / active_state_count),
                "fraction_of_active_columns": float(
                    len({(int(item[1]), int(item[2])) for item in indices})
                    / active_column_count
                ),
                "minimum_total_minus_drysmc_m3_m3": float(np.min(margin[active3])),
                "maximum_violation_to_tolerance_ratio": float(
                    max(0.0, -np.min(margin[active3]) / tolerance)
                ),
                "counts_by_zero_based_layer": counts(indices[:, 0]) if indices.size else {},
                "counts_by_soil_type": counts(
                    soil_type[tuple(indices.T)]
                ) if indices.size else {},
            })
        time_series[method] = method_series

    first_time = min(total["relative_saturation"])
    final_time = max(total["relative_saturation"])
    initial_margin = total["relative_saturation"][first_time] - dry
    initialized_at_dry = (np.abs(initial_margin) <= 1.0e-8) & active3
    final_margin = total["relative_saturation"][final_time] - dry
    final_violation = (final_margin < -tolerance) & active3
    final_indices = np.argwhere(final_violation)
    locations = []
    for layer, y_index, x_index in sorted(
        final_indices,
        key=lambda item: float(final_margin[tuple(item)]),
    ):
        layer, y_index, x_index = int(layer), int(y_index), int(x_index)
        soil = int(soil_type[layer, y_index, x_index])
        item = {
            "zero_based_layer": layer,
            "y_index": y_index,
            "x_index": x_index,
            "latitude": float(latitude[y_index, x_index]),
            "longitude": float(longitude[y_index, x_index]),
            "soil_type": soil,
            "soil_name": SOIL_NAMES.get(soil, "unknown"),
            "landuse": int(landuse[y_index, x_index]),
            "drysmc_m3_m3": float(dry[layer, y_index, x_index]),
            "relative_saturation_initial_m3_m3": float(
                total["relative_saturation"][first_time][layer, y_index, x_index]
            ),
            "relative_saturation_final_m3_m3": float(
                total["relative_saturation"][final_time][layer, y_index, x_index]
            ),
            "final_total_minus_drysmc_m3_m3": float(
                final_margin[layer, y_index, x_index]
            ),
            "final_violation_to_tolerance_ratio": float(
                -final_margin[layer, y_index, x_index] / tolerance
            ),
        }
        if "smi" in total:
            item["smi_final_m3_m3"] = float(
                total["smi"][final_time][layer, y_index, x_index]
            )
        locations.append(item)

    final_pair = assessment["comparisons"][-1]["smi_minus_relative_saturation"]
    selected_pair_fields = (
        "soil_water_content", "soil_water_content_liq", "soil_column_total_water",
        "soil_temperature", "tsfe", "taix", "swet", "hfss", "hfls", "hus2m",
        "u10m", "v10m", "hpbl",
    )
    selected_source_fields = (
        "temperature_2m_height_adjusted_k", "specific_humidity_2m",
        "wind_speed_10m_m_s", "snow_height_m", "swe_kg_m2",
    )
    tolerance_sensitivity = {}
    for multiplier in (1, 2, 5, 10):
        diagnostic_tolerance = multiplier * tolerance
        tolerance_sensitivity[str(multiplier)] = {
            "tolerance_m3_m3": diagnostic_tolerance,
            "final_count_below_tolerance": int(np.count_nonzero(
                (final_margin < -diagnostic_tolerance) & active3
            )),
        }

    payload = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "POST_HOC_DIAGNOSTIC_ONLY",
        "frozen_decision_unchanged": assessment["decision"],
        "frozen_decision_basis_unchanged": assessment["decision_basis"],
        "assessment": {
            "path": str(args.assessment.resolve()),
            "sha256": assessor.sha256(args.assessment),
        },
        "frozen_hydraulic_tolerance_m3_m3": tolerance,
        "support": {
            "active_soil_state_count": active_state_count,
            "active_column_count": active_column_count,
        },
        "parameter_context": {
            "table_label": "DRYSMC: dry soil moisture threshold",
            "drysmc_equals_wltsmc_for_all_19_stas_categories": bool(
                np.array_equal(hydraulics["DRYSMC"], hydraulics["WLTSMC"])
            ),
            "interpretation_guardrail": (
                "This post-hoc diagnostic describes threshold sensitivity only; "
                "it does not revise the frozen viability decision."
            ),
        },
        "time_series": time_series,
        "relative_saturation_initial_at_drysmc": {
            "count": int(np.count_nonzero(initialized_at_dry)),
            "fraction_of_active_soil_states": float(
                np.count_nonzero(initialized_at_dry) / active_state_count
            ),
            "counts_by_zero_based_layer": counts(
                np.argwhere(initialized_at_dry)[:, 0]
            ),
            "counts_by_soil_type": counts(soil_type[initialized_at_dry]),
            "final_violations_from_this_set": int(np.count_nonzero(
                final_violation & initialized_at_dry
            )),
        },
        "relative_saturation_final_violation_locations": locations,
        "relative_saturation_final_tolerance_sensitivity": tolerance_sensitivity,
        "final_smi_minus_relative_saturation": {
            name: final_pair[name] for name in selected_pair_fields
        },
        "source_consistency_compact": {
            method: {
                name: {
                    "bias": assessment["source_consistency"][method]["metrics"][name]["bias"],
                    "root_mean_squared_error": assessment["source_consistency"][method]["metrics"][name]["root_mean_squared_error"],
                }
                for name in selected_source_fields
            }
            for method in methods
        },
    }
    assessor.atomic_json(args.output, payload)
    Path(f"{args.output}.ready").touch()
    print(json.dumps({
        "status": payload["status"],
        "frozen_decision": payload["frozen_decision_unchanged"],
        "final_relative_saturation_violations": len(locations),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
