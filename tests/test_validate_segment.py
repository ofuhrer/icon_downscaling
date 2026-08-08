from datetime import datetime
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_segment",
    ROOT / "case_studies" / "swiss_200m" / "validation" / "validate_segment.py",
)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)
expected_output_times = VALIDATOR.expected_output_times


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
