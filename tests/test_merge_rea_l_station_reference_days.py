import csv
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge_rea_l_station_reference_days.py"
SPEC = importlib.util.spec_from_file_location("merge_rea_l_station_reference_days", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


FIELDS = [
    "valid_time",
    "station_key",
    "source_cell",
    "ta2m_ref",
    "precipitation_interval_ref",
]


def write_day(path, start, *, changed_join=False, nonzero_baseline=False):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for hour in range(25):
            valid = start + timedelta(hours=hour)
            for station_index, station in enumerate(("AAA:1", "BBB:2")):
                temperature = valid.timestamp() / 3600.0 + station_index
                if changed_join and hour == 0 and station == "AAA:1":
                    temperature += 1.0
                writer.writerow(
                    {
                        "valid_time": valid.isoformat().replace("+00:00", "Z"),
                        "station_key": station,
                        "source_cell": station_index,
                        "ta2m_ref": temperature,
                        "precipitation_interval_ref": (
                            1.0 if hour > 0 or nonzero_baseline else 0.0
                        ),
                    }
                )


def test_merge_preserves_prior_day_midnight_precipitation(tmp_path):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_day(first, start)
    write_day(second, start + timedelta(days=1))

    header, rows, station_count = MODULE.merge_days(
        [first, second], start, start + timedelta(days=2)
    )

    assert header == FIELDS
    assert station_count == 2
    assert len(rows) == 49 * 2
    join = [
        row
        for row in rows
        if row["valid_time"] == "2020-01-02T00:00:00Z"
    ]
    assert {float(row["precipitation_interval_ref"]) for row in join} == {1.0}


def test_merge_rejects_changed_join_state(tmp_path):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_day(first, start)
    write_day(second, start + timedelta(days=1), changed_join=True)

    with pytest.raises(ValueError, match="duplicate join record differs"):
        MODULE.merge_days([first, second], start, start + timedelta(days=2))


def test_merge_rejects_nonzero_next_day_baseline(tmp_path):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_day(first, start)
    write_day(second, start + timedelta(days=1), nonzero_baseline=True)

    with pytest.raises(ValueError, match="baseline precipitation is not zero"):
        MODULE.merge_days([first, second], start, start + timedelta(days=2))


def test_merge_requires_exact_daily_count(tmp_path):
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    day = tmp_path / "day.csv"
    write_day(day, start)

    with pytest.raises(ValueError, match="input count"):
        MODULE.merge_days([day], start, start + timedelta(days=2))
