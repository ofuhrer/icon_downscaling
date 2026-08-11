import importlib.util
import json
import math
from pathlib import Path
import sys

import netCDF4
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_station_wind_footprints.py"
SPEC = importlib.util.spec_from_file_location("station_wind_footprints", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_inputs(tmp_path, *, report_times=None, report_elevation=1_000.0):
    static = tmp_path / "static.nc"
    previous_output = tmp_path / "previous_segment.nc"
    current_output = tmp_path / "current_segment.nc"
    observations = tmp_path / "observations.csv"
    evaluator = tmp_path / "evaluator.json"
    diagnostic = tmp_path / "diagnostic.json"

    y, x = np.mgrid[:7, :7]
    latitude = 46.0 + 0.01 * y - 0.005 * x
    longitude = 7.0 + 0.01 * x
    terrain = np.full((7, 7), 500.0, dtype=np.float32)
    terrain[3, 3] = 1_000.0
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 7)
        dataset.createDimension("x", 7)
        dataset.hicar_dx_m = 200.0
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitude
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitude
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = terrain

    sine, cosine = MODULE.hicar_grid_rotation(latitude, longitude, dx_m=200.0)
    earth_u = np.ones((7, 7), dtype=np.float64)
    earth_u[3, 3] = 3.0
    grid_u = earth_u * cosine
    grid_v = earth_u * sine

    def write_output(path, hours):
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.createDimension("time", len(hours))
            dataset.createDimension("y", 7)
            dataset.createDimension("x", 7)
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = "hours since 2020-10-02 00:00:00"
            time[:] = np.asarray(hours) + 0.432 / 3600.0
            dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitude
            dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitude
            dataset.createVariable("u10m", "f4", ("time", "y", "x"))[:] = grid_u
            dataset.createVariable("v10m", "f4", ("time", "y", "x"))[:] = grid_v

    write_output(previous_output, np.arange(6) / 6.0)
    write_output(current_output, [1.0])

    header = [
        "meas_site", "termin", "nat_abbr",
        "fkl010h0", "pi", "mi", "dq", "uc",
        "dkl010h0", "pi", "mi", "dq", "uc",
    ]
    rows = [
        ["1", "20201002000000", "RID", "1.0", "", "", "4", "", "270", "", "", "4", ""],
        ["1", "20201002010000", "RID", "1.0", "", "", "4", "", "270", "", "", "4", ""],
    ]
    observations.write_text(
        ";".join(header)
        + "\n"
        + "\n".join(";".join(row) for row in rows)
        + "\n"
    )

    matched_times = report_times or [
        "2020-10-02T00:00:00+00:00",
        "2020-10-02T01:00:00+00:00",
    ]
    def vector_metric(count, value):
        return {
            "count": count,
            "vector_root_mean_squared_error_m_s": value,
        }
    evaluator.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "issues": [],
                "matched_model_times": matched_times,
                "station_mapping": {
                    "sites": [
                        {
                            "key": "RID:1",
                            "abbreviation": "RID",
                            "meas_site": "1",
                            "latitude": float(latitude[3, 3]),
                            "longitude": float(longitude[3, 3]),
                            "station_elevation_m": 2_200.0,
                            "hicar_elevation_m": report_elevation,
                            "nearest_cell_distance_km": 0.0,
                            "terrain_relative_elevation_m": 200.0,
                            "hicar_y_index": 3,
                            "hicar_x_index": 3,
                        }
                    ]
                },
                "site_metrics": {
                    "RID:1": {
                        "hicar": {"wind_vector": vector_metric(1, 3.0)},
                        "rea_l": {"wind_vector": vector_metric(1, 1.0)},
                    }
                },
            }
        )
    )
    return static, (previous_output, current_output), observations, evaluator, diagnostic


def arguments(static, outputs, observations, evaluator, diagnostic):
    result = [
        "--evaluator-report", str(evaluator),
        "--static-file", str(static),
        "--observations", str(observations),
        "--report", str(diagnostic),
        "--include-optimistic-best-cell",
    ]
    for output in outputs:
        result.extend(["--output-file", str(output)])
    return result


def test_streaming_footprint_metrics_and_quality_flags(tmp_path):
    inputs = make_inputs(tmp_path)

    assert MODULE.main(arguments(*inputs)) == 0

    payload = json.loads(inputs[-1].read_text())
    assert payload["selection"]["selected_site_count"] == 1
    site = payload["sites"][0]
    assert set(site["selection_reasons"]) == {
        "terrain_ridge_relative_gt_150m",
        "station_elevation_ge_2000m",
        "worst_5_hicar_minus_rea_l_vector_rmse",
    }
    small = site["footprints"]["0.4"]
    assert small["geometry"]["expected_cell_count"] == 13
    assert small["geometry"]["too_small"] is False
    assert small["valid_observation_count"] == 1
    assert math.isclose(
        small["nearest_cell"]["vector_rmse_m_s"], 2.0, rel_tol=1.0e-6
    )
    assert math.isclose(
        small["footprint_mean_vector"]["vector_rmse_m_s"],
        2.0 / 13.0,
        rel_tol=1.0e-6,
    )
    distribution = small["fixed_cell_vector_rmse_distribution_m_s"]
    assert distribution["complete_cell_count"] == 13
    assert math.isclose(distribution["median"], 0.0, abs_tol=1.0e-6)
    assert math.isclose(distribution["p90"], 0.0, abs_tol=1.0e-6)
    assert math.isclose(
        small["optimistic_post_hoc_best_fixed_cell"]["vector_rmse_m_s"],
        0.0,
        abs_tol=1.0e-6,
    )
    assert site["footprints"]["1"]["geometry"]["too_small"] is True
    assert payload["data_quality"]["required_ten_minute_samples_complete"] is True
    assert payload["data_quality"]["scored_hour_count"] == 1
    assert payload["data_quality"]["required_ten_minute_sample_count"] == 6
    assert payload["data_quality"]["unused_input_time_count"] == 1
    assert payload["data_quality"]["missing_qc_observation_times_by_selected_site"] == {}


def test_rejects_static_report_grid_identity_mismatch(tmp_path):
    inputs = make_inputs(tmp_path, report_elevation=999.0)

    with pytest.raises(ValueError, match="static/report elevation mismatch"):
        MODULE.main(arguments(*inputs))

    assert not inputs[-1].exists()


def test_rejects_missing_ten_minute_samples_for_evaluator_hour(tmp_path):
    inputs = make_inputs(
        tmp_path,
        report_times=[
            "2020-10-02T00:00:00+00:00",
            "2020-10-02T02:00:00+00:00",
        ],
    )

    with pytest.raises(ValueError, match="lacks evaluator-required ten-minute"):
        MODULE.main(arguments(*inputs))

    assert not inputs[-1].exists()
