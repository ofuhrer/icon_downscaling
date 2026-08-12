from datetime import datetime
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

import netCDF4
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_segment",
    ROOT / "case_studies" / "swiss_200m" / "validation" / "validate_segment.py",
)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)
expected_output_times = VALIDATOR.expected_output_times

COMPARE_SPEC = importlib.util.spec_from_file_location(
    "compare_restarts",
    ROOT / "case_studies" / "swiss_200m" / "validation" / "compare_restarts.py",
)
COMPARATOR = importlib.util.module_from_spec(COMPARE_SPEC)
assert COMPARE_SPEC.loader is not None
COMPARE_SPEC.loader.exec_module(COMPARATOR)


def test_cold_start_output_includes_segment_start() -> None:
    start = datetime(2020, 2, 10, 0)
    end = datetime(2020, 2, 10, 2)
    assert expected_output_times(start, end, 3600, continued=False) == [
        datetime(2020, 2, 10, 0),
        datetime(2020, 2, 10, 1),
        datetime(2020, 2, 10, 2),
    ]


def test_restart_output_omits_predecessor_terminal_time() -> None:
    start = datetime(2020, 2, 10, 1)
    end = datetime(2020, 2, 10, 2)
    assert expected_output_times(start, end, 3600, continued=True) == [
        datetime(2020, 2, 10, 2)
    ]


def test_reference_surface_coupling_is_required() -> None:
    assert VALIDATOR.REQUIRED_PHYSICS["lsm.nmp_opt_sfc"] == "1"
    assert VALIDATOR.REQUIRED_PHYSICS["sfc.iz0tlnd"] == "1"
    assert VALIDATOR.REQUIRED_PHYSICS["pbl.ysu_topdown_pblmix"] == "0"
    assert VALIDATOR.REQUIRED_PHYSICS["rad.terrain_reflected_sw"] == "F"
    assert VALIDATOR.REQUIRED_PHYSICS["rad.terrain_longwave"] == "F"


def test_expected_radiation_scheme_is_selected_per_run() -> None:
    assert VALIDATOR.expected_physics("rrtmg")["physics.rad"] == "RRTMG"
    assert VALIDATOR.expected_physics("rrtmgp")["physics.rad"] == "RRTMGP"


def test_numeric_restart_attributes_fail_closed() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "restart.nc"
        with netCDF4.Dataset(path, "w") as dataset:
            dataset.setncattr("present", 20.0)
            dataset.setncattr("nan_value", np.nan)
            dataset.setncattr("text_value", "twenty")
        with netCDF4.Dataset(path) as dataset:
            mismatches = VALIDATOR.numeric_attribute_mismatches(
                dataset,
                {
                    "present": 20.0,
                    "missing": 20.0,
                    "nan_value": 20.0,
                    "text_value": 20.0,
                },
            )

    assert "present" not in mismatches
    assert mismatches["missing"]["actual"] == "missing"
    assert mismatches["nan_value"]["actual"] == "nan"
    assert mismatches["text_value"]["actual"] == "twenty"


def test_restart_comparison_excludes_three_cell_guard_region() -> None:
    class Variable:
        def __init__(self, values: np.ndarray):
            self.values = values

        def __getitem__(self, key):
            return self.values[key]

    values = np.ones((2, 10, 10), dtype=np.float32)
    values[:, 3:-3, 3:-3] = 0.0
    core = COMPARATOR.core_values(Variable(values))

    assert core.shape == (2, 4, 4)
    assert not np.any(core)
