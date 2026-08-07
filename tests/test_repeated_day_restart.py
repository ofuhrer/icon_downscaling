from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import netCDF4
import numpy as np

from case_studies.swiss_200m.wind_climatology.relabel_repeated_day_restart import (
    encoded_time,
    publish_relabelled_restart,
)
from case_studies.swiss_200m.wind_climatology.publish_repeated_day_restart_compatibility import (
    publish as publish_compatibility,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_restart(path: Path, when: datetime) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", 1)
        dataset.createDimension("x", 3)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "days since 1800-01-01 00:00:00"
        time.calendar = "proleptic_gregorian"
        time[:] = netCDF4.date2num(when, time.units, time.calendar)
        state = dataset.createVariable("potential_temperature", "f4", ("time", "x"))
        state[:] = np.array([[280.0, 281.0, 282.0]], dtype=np.float32)


def test_relabel_changes_clock_and_preserves_state(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    source_report = tmp_path / "source.json"
    target = tmp_path / "target.nc"
    report = tmp_path / "target.json"
    make_restart(source, datetime(2020, 7, 2, 1))
    source_report.write_text(
        json.dumps(
            {
                "status": "PASS",
                "end": "2020-07-02T01:00:00",
                "restart": {"path": str(source), "sha256": digest(source)},
                "provenance": {"source_commit": "abc123"},
            }
        )
    )
    Path(f"{source_report}.ready").touch()

    payload = publish_relabelled_restart(
        source=source,
        source_report=source_report,
        target=target,
        target_time=datetime(2020, 7, 1, 1),
        expected_source_commit="abc123",
        report_path=report,
    )

    actual, _, _, _ = encoded_time(target)
    assert actual == datetime(2020, 7, 1, 1)
    with netCDF4.Dataset(source) as before, netCDF4.Dataset(target) as after:
        np.testing.assert_array_equal(
            before.variables["potential_temperature"][:],
            after.variables["potential_temperature"][:],
        )
    assert payload["clock_transform"]["shift_seconds"] == -86400.0
    assert payload["sha256"] == digest(target)
    assert payload["end"] == "2020-07-01T01:00:00"
    assert payload["restart"]["path"] == str(target.resolve())
    assert payload["restart"]["sha256"] == payload["sha256"]
    assert payload["provenance"]["source_commit"] == "abc123"
    assert Path(f"{target}.ready").is_file()
    assert Path(f"{report}.ready").is_file()


def test_relabel_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.nc"
    source_report = tmp_path / "source.json"
    target = tmp_path / "target.nc"
    report = tmp_path / "target.json"
    make_restart(source, datetime(2020, 1, 16))
    source_report.write_text(
        json.dumps(
            {
                "status": "PASS",
                "end": "2020-01-16T00:00:00",
                "restart": {"path": str(source), "sha256": digest(source)},
                "provenance": {"source_commit": "abc123"},
            }
        )
    )
    Path(f"{source_report}.ready").touch()
    kwargs = dict(
        source=source,
        source_report=source_report,
        target=target,
        target_time=datetime(2020, 1, 15),
        expected_source_commit="abc123",
        report_path=report,
    )
    first = publish_relabelled_restart(**kwargs)
    second = publish_relabelled_restart(**kwargs)
    assert first == second


def test_legacy_transform_can_be_adapted_for_chunk_validator(tmp_path: Path) -> None:
    restart = tmp_path / "restart.nc"
    restart.write_bytes(b"restart-state")
    transform = tmp_path / "transform.json"
    transform.write_text(
        json.dumps(
            {
                "status": "PASS",
                "checkpoint": str(restart),
                "checkpoint_time": "2020-07-01T01:00:00",
                "expected_source_commit": "abc123",
                "sha256": digest(restart),
            }
        )
    )
    Path(f"{transform}.ready").touch()
    output = tmp_path / "compatibility.json"
    payload = publish_compatibility(transform, restart, output)
    assert payload["end"] == "2020-07-01T01:00:00"
    assert payload["restart"]["sha256"] == digest(restart)
    assert payload["provenance"]["source_commit"] == "abc123"
    assert Path(f"{output}.ready").is_file()
