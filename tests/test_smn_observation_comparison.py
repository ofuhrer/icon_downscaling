import importlib.util
from datetime import timedelta
import json
import math
from pathlib import Path
import subprocess
import sys

import netCDF4
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "compare_hicar_rea_l_to_smn.py"
)
SPEC = importlib.util.spec_from_file_location("smn_comparison", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_exact_integral_lead_hour_accepts_only_nonnegative_whole_hours():
    start = MODULE.parse_time("20200701000000")

    assert MODULE.exact_integral_lead_hour(start, start) == 0
    assert MODULE.exact_integral_lead_hour(start + timedelta(hours=27), start) == 27


def test_exact_integral_lead_hour_rejects_sub_hour_and_negative_leads():
    start = MODULE.parse_time("20200701000000")

    for valid in (
        start + timedelta(minutes=30),
        start + timedelta(hours=1, seconds=1),
        start - timedelta(hours=1),
    ):
        try:
            MODULE.exact_integral_lead_hour(valid, start)
        except ValueError as error:
            assert "not an exact nonnegative integral-hour lead" in str(error)
        else:
            raise AssertionError(f"accepted invalid lead timestamp {valid.isoformat()}")


def test_pair_statistics_exposes_minimal_error_anatomy():
    statistics = MODULE.PairStatistics()
    for model, observation in ((2.0, 1.0), (4.0, 2.0), (6.0, 3.0)):
        statistics.add(model, observation)

    result = statistics.result()
    assert result["count"] == 3
    assert result["bias"] == 2.0
    assert result["mean_absolute_error"] == 2.0
    assert result["root_mean_squared_error"] == pytest.approx(
        math.sqrt(14.0 / 3.0)
    )
    assert result["centered_root_mean_squared_error"] == pytest.approx(
        math.sqrt(2.0 / 3.0)
    )
    assert result["model_standard_deviation"] == pytest.approx(
        math.sqrt(8.0 / 3.0)
    )
    assert result["observation_standard_deviation"] == pytest.approx(
        math.sqrt(2.0 / 3.0)
    )
    assert result["correlation"] == pytest.approx(1.0)


def test_pair_statistics_does_not_invent_constant_series_correlation():
    statistics = MODULE.PairStatistics()
    for observation in (1.0, 2.0, 3.0):
        statistics.add(4.0, observation)

    result = statistics.result()
    assert result["model_standard_deviation"] == 0.0
    assert result["observation_standard_deviation"] > 0.0
    assert result["correlation"] is None


def test_retrieval_script_uses_smn_group_and_cluster_configuration():
    script = (
        ROOT
        / "case_studies"
        / "swiss_200m"
        / "scripts"
        / "retrieve_smn_event_observations_balfrin.sbatch"
    ).read_text()
    assert "export OPR_HOME=${JRETRIEVE_CONFIG_HOME:-/oprusers/osm}" in script
    assert "--groups SMN" in script
    assert "--data-quality-cat-nr-limit 4" in script


def test_observation_reader_keeps_distinct_measurement_sites(tmp_path):
    path = tmp_path / "observations.csv"
    header = [
        "meas_site",
        "termin",
        "latitude",
        "longitude",
        "elev",
        "nat_abbr",
    ]
    for parameter in MODULE.OBSERVATION_PARAMETERS:
        header.extend([parameter, "pi", "mi", "dq", "uc"])
    rows = []
    for meas_site, wind_speed, quality in (
        ("10", "", ""),
        ("11", "3.0", "4"),
    ):
        row = [
            meas_site,
            "20200701030000",
            "46.8",
            "8.2",
            "500",
            "ABC",
        ]
        for parameter in MODULE.OBSERVATION_PARAMETERS:
            value = wind_speed if parameter == "fkl010h0" else ""
            dq = quality if parameter == "fkl010h0" else ""
            row.extend([value, "0.999" if value else "", "0", dq, ""])
        rows.append(row)
    path.write_text(
        ";".join(header)
        + "\n"
        + "\n".join(";".join(row) for row in rows)
        + "\n"
    )

    sites, observations, inventory = MODULE.read_observations(path)

    assert set(sites) == {"ABC:10", "ABC:11"}
    assert inventory["site_count"] == 2
    valid = MODULE.parse_time("20200701030000")
    assert observations[valid]["ABC:11"]["fkl010h0"] == 3.0
    assert "fkl010h0" not in observations[valid]["ABC:10"]


def test_nearest_hicar_cells_refines_coarse_search():
    latitude = np.repeat(np.linspace(46.0, 46.1, 41)[:, None], 61, axis=1)
    longitude = np.repeat(np.linspace(7.0, 7.2, 61)[None, :], 41, axis=0)
    site = MODULE.Site("1", "ABC", 46.052, 7.103, 500.0)

    y_index, x_index, distance = MODULE.nearest_hicar_cells(
        latitude, longitude, [site], stride=10
    )

    assert abs(int(y_index[0]) - 21) <= 1
    assert abs(int(x_index[0]) - 31) <= 1
    assert distance[0] < 0.25


def test_station_selection_excludes_sites_outside_hicar_domain():
    sites = [
        MODULE.Site("1", "IN", 46.0, 7.0, 500.0),
        MODULE.Site("2", "OUT", 48.0, 9.0, 500.0),
    ]
    selected, y_index, x_index, distance, excluded = (
        MODULE.select_sites_by_distance(
            sites,
            np.array([2, 0]),
            np.array([3, 0]),
            np.array([0.12, 108.0]),
            maximum_distance_km=1.0,
        )
    )

    assert [site.key for site in selected] == ["IN:1"]
    np.testing.assert_array_equal(y_index, [2])
    np.testing.assert_array_equal(x_index, [3])
    np.testing.assert_allclose(distance, [0.12])
    assert excluded == [
        {"key": "OUT:2", "nearest_cell_distance_km": 108.0}
    ]


def test_wind_and_thermodynamic_conversions():
    u = np.array([0.0, -1.0, 0.0, 1.0])
    v = np.array([-1.0, 0.0, 1.0, 0.0])
    np.testing.assert_allclose(
        MODULE.wind_direction_from(u, v),
        [0.0, 90.0, 180.0, 270.0],
    )
    temperature = np.array([293.15])
    pressure = np.array([100000.0])
    saturation_vapor_pressure = 611.2 * np.exp(17.67 * 20.0 / (20.0 + 243.5))
    mixing_ratio = MODULE.EPSILON * saturation_vapor_pressure / (
        pressure[0] - saturation_vapor_pressure
    )
    specific_humidity = np.array([mixing_ratio / (1.0 + mixing_ratio)])
    np.testing.assert_allclose(
        MODULE.relative_humidity_percent(
            temperature, specific_humidity, pressure
        ),
        [100.0],
        rtol=1.0e-6,
    )


def test_circular_error_wraps_at_north():
    statistics = MODULE.CircularStatistics()
    statistics.add(1.0, 359.0)
    statistics.add(359.0, 1.0)
    result = statistics.result()
    assert result["count"] == 2
    assert result["mean_absolute_circular_error_degrees"] == 2.0
    assert abs(result["circular_bias_degrees"]) < 1.0e-12


def test_common_triplets_exclude_both_models_when_either_is_nonfinite():
    classes = {"all_sites": np.array([True])}
    accumulators = MODULE.create_accumulators(classes)
    accounting = {}
    direction = float(
        MODULE.wind_direction_from(np.array([3.0]), np.array([4.0]))[0]
    )
    observation = {
        "temperature_2m_height_adjusted_k": 280.0,
        "u_wind_10m_m_s": 3.0,
        "v_wind_10m_m_s": 4.0,
        "wind_speed_10m_m_s": 5.0,
        "wind_direction_degrees": direction,
        "precipitation_interval_kg_m2": 1.0,
    }
    hicar = {
        "temperature_2m_height_adjusted_k": 281.0,
        "u_wind_10m_m_s": 3.0,
        "v_wind_10m_m_s": 4.0,
        "wind_speed_10m_m_s": 5.0,
        "wind_direction_degrees": direction,
        "precipitation_interval_kg_m2": 2.0,
    }
    rea_l = {
        "temperature_2m_height_adjusted_k": 282.0,
        "u_wind_10m_m_s": 6.0,
        "v_wind_10m_m_s": 8.0,
        "wind_speed_10m_m_s": 10.0,
        "wind_direction_degrees": direction,
        "precipitation_interval_kg_m2": 3.0,
    }
    common = MODULE.select_common_site_values(
        hicar, rea_l, observation, accounting
    )
    MODULE.add_common_site_values(accumulators, classes, 0, common)

    hicar_bad = dict(hicar)
    hicar_bad["temperature_2m_height_adjusted_k"] = math.nan
    rea_l_bad = dict(rea_l)
    rea_l_bad["u_wind_10m_m_s"] = math.nan
    rea_l_bad["wind_speed_10m_m_s"] = math.nan
    rea_l_bad["precipitation_interval_kg_m2"] = math.nan
    common = MODULE.select_common_site_values(
        hicar_bad, rea_l_bad, observation, accounting
    )
    MODULE.add_common_site_values(accumulators, classes, 0, common)

    results = MODULE.accumulator_results(accumulators)
    for source in ("hicar", "rea_l"):
        assert results[source]["all_sites"][
            "temperature_2m_height_adjusted_k"
        ]["count"] == 1
        assert results[source]["all_sites"]["wind_speed_10m_m_s"]["count"] == 1
        assert results[source]["all_sites"]["wind_vector"]["count"] == 1
        assert results[source]["all_sites"][
            "precipitation_interval_kg_m2"
        ]["count"] == 1
    counts = MODULE.common_triplet_accounting_results(accounting)
    assert counts["temperature_2m_height_adjusted_k"]["exclusions"] == {
        "hicar_missing_or_nonfinite": 1
    }
    for metric in (
        "wind_speed_10m_m_s",
        "wind_vector",
        "precipitation_interval_kg_m2",
    ):
        assert counts[metric]["candidate_station_time_count"] == 2
        assert counts[metric]["accepted_common_triplet_count"] == 1
        assert counts[metric]["exclusions"] == {
            "rea_l_missing_or_nonfinite": 1
        }


def test_wind_direction_mask_depends_only_on_observed_speed():
    accounting = {}
    base = {
        "wind_direction_degrees": 90.0,
        "wind_speed_10m_m_s": 5.0,
    }
    hicar = dict(base, wind_speed_10m_m_s=0.1)
    rea_l = dict(base, wind_speed_10m_m_s=0.2)
    accepted = MODULE.select_common_site_values(hicar, rea_l, base, accounting)
    assert accepted["wind_direction"] is not None

    calm_observation = dict(base, wind_speed_10m_m_s=2.49)
    rejected = MODULE.select_common_site_values(
        base, base, calm_observation, accounting
    )
    assert rejected["wind_direction"] is None
    counts = MODULE.common_triplet_accounting_results(accounting)["wind_direction"]
    assert counts["accepted_common_triplet_count"] == 1
    assert counts["exclusions"] == {"observation_calm_wind_direction_mask": 1}


def test_full_station_comparison_reports_exact_synthetic_match(tmp_path):
    static = tmp_path / "static.nc"
    output = tmp_path / "output.nc"
    reference_list = tmp_path / "reference_list.txt"
    observations = tmp_path / "observations.csv"
    report = tmp_path / "report.json"

    with netCDF4.Dataset(static, "w") as dataset:
        dataset.createDimension("y", 3)
        dataset.createDimension("x", 3)
        dataset.hicar_dx_m = 200.0
        latitudes = np.repeat(np.linspace(46.0, 46.1, 3)[:, None], 3, axis=1)
        longitudes = np.repeat(np.linspace(7.0, 7.1, 3)[None, :], 3, axis=0)
        dataset.createVariable("lat", "f8", ("y", "x"))[:] = latitudes
        dataset.createVariable("lon", "f8", ("y", "x"))[:] = longitudes
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = 500.0

    with netCDF4.Dataset(output, "w") as dataset:
        dataset.createDimension("time", 2)
        dataset.createDimension("y", 3)
        dataset.createDimension("x", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-01 00:00:00"
        offset = 0.432 / 3600.0
        time[:] = [offset, 3.0 + offset]
        temperature = 280.0
        pressure = 90_000.0
        relative_humidity = 60.0
        saturation_pressure = 611.2 * np.exp(
            17.67
            * (temperature - 273.15)
            / ((temperature - 273.15) + 243.5)
        )
        vapor_pressure = relative_humidity / 100.0 * saturation_pressure
        specific_humidity = (
            MODULE.EPSILON
            * vapor_pressure
            / (pressure - (1.0 - MODULE.EPSILON) * vapor_pressure)
        )
        values = {
            "taix": [temperature, temperature],
            "psfc": [pressure, pressure],
            "hus2m": [specific_humidity, specific_humidity],
            "u10m": [3.0, 3.0],
            "v10m": [4.0, 4.0],
            "snow_height": [0.2, 0.2],
            "rsds": [100.0, 100.0],
            "precipitation": [0.0, 3.0],
        }
        for name, series in values.items():
            dataset.createVariable(name, "f4", ("time", "y", "x"))[:] = (
                np.asarray(series)[:, None, None]
            )

    reference_paths = []
    for hour, precipitation in ((0, 0.0), (3, 3.0)):
        path = tmp_path / f"reference_{hour}.nc"
        reference_paths.append(path)
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.createDimension("time", 1)
            dataset.createDimension("latitude", 2)
            dataset.createDimension("longitude", 2)
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = "hours since 2020-07-01 00:00:00"
            time[:] = hour
            dataset.createVariable("latitude", "f8", ("latitude",))[:] = [
                46.0,
                46.1,
            ]
            dataset.createVariable("longitude", "f8", ("longitude",))[:] = [
                7.0,
                7.1,
            ]
            fields = {
                "ta2m_ref": temperature,
                "psfc_ref": pressure,
                "hus2m_ref": specific_humidity,
                "u10m_ref": 3.0,
                "v10m_ref": 4.0,
                "snow_height_ref": 0.2,
                "source_terrain": 500.0,
                "precipitation_interval_ref": precipitation,
            }
            for name, value in fields.items():
                dataset.createVariable(
                    name, "f4", ("time", "latitude", "longitude")
                )[:] = value
    reference_list.write_text(
        "".join(f'"{path}"\n' for path in reference_paths)
    )

    header = [
        "meas_site",
        "termin",
        "latitude",
        "longitude",
        "elev",
        "nat_abbr",
    ]
    for parameter in MODULE.OBSERVATION_PARAMETERS:
        header.extend([parameter, "pi", "mi", "dq", "uc"])
    direction = float(MODULE.wind_direction_from(np.array([3.0]), np.array([4.0]))[0])
    rows = []
    for hour in range(4):
        values = {
            "tre200h0": temperature - 273.15,
            "ure200h0": relative_humidity,
            "prestah0": pressure / 100.0,
            "rre150h0": 1.0 if hour else 0.0,
            "fkl010h0": 5.0,
            "dkl010h0": direction,
            "gre000h0": 100.0,
            "htoauths": 20.0,
        }
        row = [
            "1",
            f"20200701{hour:02d}0000",
            "46.05",
            "7.05",
            "500",
            "ABC",
        ]
        for parameter in MODULE.OBSERVATION_PARAMETERS:
            row.extend([str(values[parameter]), "0.999", "0", "4", ""])
        rows.append(row)
    observations.write_text(
        ";".join(header)
        + "\n"
        + "\n".join(";".join(row) for row in rows)
        + "\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--event-name",
            "synthetic",
            "--static-file",
            str(static),
            "--output-file",
            str(output),
            "--reference-list",
            str(reference_list),
            "--observations",
            str(observations),
            "--minimum-core-pairs",
            "1",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(report.read_text())
    assert len(payload["matched_model_times"]) == 2
    assert sorted(payload["lead_time_metrics"]) == ["0", "3"]
    assert (
        payload["lead_time_metrics"]["3"]["hicar"]["all_sites"]
        ["wind_speed_10m_m_s"]["count"]
        == 1
    )
    assert set(payload["site_metrics"]) == {"ABC:1"}
    assert (
        payload["site_metrics"]["ABC:1"]["hicar"]
        ["temperature_2m_height_adjusted_k"]["count"]
        == 2
    )
    assert (
        payload["seasonal_metrics"]["JJA"]["hicar"]["all_sites"][
            "temperature_2m_height_adjusted_k"
        ]["count"]
        == 2
    )
    assert (
        payload["seasonal_metrics"]["DJF"]["hicar"]["all_sites"][
            "temperature_2m_height_adjusted_k"
        ]["count"]
        == 0
    )
    overall = payload["metrics"]["hicar"]["all_sites"]
    assert math.isclose(
        overall["temperature_2m_height_adjusted_k"]["bias"], 0.0, abs_tol=1e-5
    )
    assert math.isclose(
        overall["precipitation_interval_kg_m2"]["bias"], 0.0, abs_tol=1e-5
    )
    assert math.isclose(
        overall["wind_vector"]["vector_root_mean_squared_error_m_s"],
        0.0,
        abs_tol=1e-5,
    )
    accounting = payload["common_triplet_accounting"]["metrics"]
    assert accounting["temperature_2m_height_adjusted_k"] == {
        "candidate_station_time_count": 2,
        "accepted_common_triplet_count": 2,
        "excluded_station_time_count": 0,
        "exclusions": {},
    }
    assert accounting["wind_speed_10m_m_s"][
        "accepted_common_triplet_count"
    ] == 2
    assert accounting["wind_vector"]["accepted_common_triplet_count"] == 2
    assert accounting["precipitation_interval_kg_m2"] == {
        "candidate_station_time_count": 2,
        "accepted_common_triplet_count": 1,
        "excluded_station_time_count": 1,
        "exclusions": {"observation_missing_or_nonfinite": 1},
    }
