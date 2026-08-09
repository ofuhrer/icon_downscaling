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
    output = tmp_path / "output.nc"
    observations = tmp_path / "observations.csv"
    evaluator = tmp_path / "evaluator.json"
    diagnostic = tmp_path / "diagnostic.json"

    latitude = np.repeat(np.linspace(46.0, 46.06, 7)[:, None], 7, axis=1)
    longitude = np.repeat(np.linspace(7.0, 7.06, 7)[None, :], 7, axis=0)
    terrain = np.full((7, 7), 500.0, dtype=np.float32)
    terrain[3, 3] = 1_000.0
    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 7)
        dataset.createDimension("x", 7)
        dataset.hicar_dx_m = 200.0
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitude
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitude
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = terrain

    with netCDF4.Dataset(output, "w") as dataset:
        dataset.createDimension("time", 2)
        dataset.createDimension("y", 7)
        dataset.createDimension("x", 7)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-10-02 00:00:00"
        time[:] = [0.432 / 3600.0, 1.0 + 0.432 / 3600.0]
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitude
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitude
        u = np.ones((2, 7, 7), dtype=np.float32)
        u[:, 3, 3] = 3.0
        dataset.createVariable("u10m", "f4", ("time", "y", "x"))[:] = u
        dataset.createVariable("v10m", "f4", ("time", "y", "x"))[:] = 0.0

    header = [
        "meas_site", "termin", "nat_abbr",
        "fkl010h0", "pi", "mi", "dq", "uc",
        "dkl010h0", "pi", "mi", "dq", "uc",
    ]
    rows = [
        ["1", "20201002000000", "RID", "1.0", "", "", "4", "", "270", "", "", "4", ""],
        ["1", "20201002010000", "RID", "1.0", "", "", "3", "", "270", "", "", "4", ""],
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
                "schema_version": 1,
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
                        "hicar": {"wind_vector": vector_metric(2, 3.0)},
                        "rea_l": {"wind_vector": vector_metric(2, 1.0)},
                    }
                },
            }
        )
    )
    return static, output, observations, evaluator, diagnostic


def arguments(static, output, observations, evaluator, diagnostic):
    return [
        "--evaluator-report", str(evaluator),
        "--static-file", str(static),
        "--output-file", str(output),
        "--observations", str(observations),
        "--report", str(diagnostic),
        "--include-optimistic-best-cell",
    ]


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
    assert math.isclose(small["nearest_cell"]["vector_rmse_m_s"], 2.0)
    assert math.isclose(
        small["footprint_mean_vector"]["vector_rmse_m_s"], 2.0 / 13.0
    )
    distribution = small["fixed_cell_vector_rmse_distribution_m_s"]
    assert distribution["complete_cell_count"] == 13
    assert math.isclose(distribution["median"], 0.0, abs_tol=1.0e-12)
    assert math.isclose(distribution["p90"], 0.0, abs_tol=1.0e-12)
    assert math.isclose(
        small["optimistic_post_hoc_best_fixed_cell"]["vector_rmse_m_s"],
        0.0,
        abs_tol=1.0e-12,
    )
    assert site["footprints"]["1"]["geometry"]["too_small"] is True
    assert payload["data_quality"]["model_times_exactly_match_evaluator"] is True
    assert payload["data_quality"]["missing_qc_observation_times_by_selected_site"] == {
        "RID:1": ["2020-10-02T01:00:00+00:00"]
    }


def test_rejects_static_report_grid_identity_mismatch(tmp_path):
    inputs = make_inputs(tmp_path, report_elevation=999.0)

    with pytest.raises(ValueError, match="static/report elevation mismatch"):
        MODULE.main(arguments(*inputs))

    assert not inputs[-1].exists()


def test_rejects_model_times_that_do_not_exactly_match_evaluator(tmp_path):
    inputs = make_inputs(
        tmp_path, report_times=["2020-10-02T00:00:00+00:00"]
    )

    with pytest.raises(ValueError, match="do not exactly equal"):
        MODULE.main(arguments(*inputs))

    assert not inputs[-1].exists()
