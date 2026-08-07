#!/usr/bin/env python3
"""Assess the frozen ICON REA-L-only Plateau calm/stability selection gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np


TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
KAPPA = 287.05 / 1004.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_published(path: Path, label: str) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def variable(dataset: netCDF4.Dataset, *names: str) -> netCDF4.Variable:
    for name in names:
        if name in dataset.variables:
            return dataset.variables[name]
    raise ValueError(f"none of the variables {names} exists in {dataset.filepath()}")


def values(dataset: netCDF4.Dataset, *names: str) -> np.ndarray:
    data = variable(dataset, *names)[:]
    return np.asarray(np.ma.filled(data, np.nan), dtype=np.float64).squeeze()


def valid_time(dataset: netCDF4.Dataset) -> datetime:
    time = variable(dataset, "time")
    decoded = netCDF4.num2date(
        time[:],
        time.units,
        calendar=getattr(time, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    item = np.atleast_1d(decoded)[0]
    return datetime(item.year, item.month, item.day, item.hour, item.minute, item.second)


def horizontal_coordinates(dataset: netCDF4.Dataset) -> tuple[np.ndarray, np.ndarray]:
    lat = values(dataset, "lat", "latitude", "lat_1")
    lon = values(dataset, "lon", "longitude", "lon_1")
    if lat.ndim == 1 and lon.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    if lat.shape != lon.shape:
        raise ValueError("latitude and longitude shapes differ")
    return lat, lon


def assess(
    *,
    contract_path: Path,
    plan_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    require_published(contract_path, "Plateau gate contract")
    require_published(plan_path, "Plateau forcing plan")
    contract = json.loads(contract_path.read_text())
    plan = json.loads(plan_path.read_text())
    period = contract["period"]
    start = datetime.strptime(period["start_exclusive"], TIME_FORMAT)
    end = datetime.strptime(period["end_inclusive"], TIME_FORMAT)
    cadence = timedelta(seconds=int(period["cadence_seconds"]))
    expected = []
    cursor = start + cadence
    while cursor <= end:
        expected.append(cursor)
        cursor += cadence
    if len(expected) != int(period["expected_hours"]):
        raise ValueError("gate contract period is internally inconsistent")

    records = {
        datetime.strptime(item["valid_time"], TIME_FORMAT): Path(
            item["forcing_file"]
        )
        for item in plan["records"]
    }
    paths = []
    for timestamp in expected:
        if timestamp not in records:
            raise ValueError(f"forcing plan lacks {timestamp:{TIME_FORMAT}}")
        path = records[timestamp]
        require_published(path, "Plateau forcing record")
        paths.append(path)

    mask_spec = contract["plateau_mask"]
    hourly = []
    mask_count: int | None = None
    for expected_time, path in zip(expected, paths):
        with netCDF4.Dataset(path) as dataset:
            actual_time = valid_time(dataset)
            if actual_time != expected_time:
                raise ValueError(
                    f"{path} time {actual_time:{TIME_FORMAT}} != "
                    f"{expected_time:{TIME_FORMAT}}"
                )
            lat, lon = horizontal_coordinates(dataset)
            land = values(dataset, "FR_LAND", "landmask")
            terrain = values(dataset, "HSURF", "topo_driving", "topo")
            mask = (
                np.isfinite(lat)
                & np.isfinite(lon)
                & np.isfinite(land)
                & np.isfinite(terrain)
                & (land >= float(mask_spec["minimum_land_fraction"]))
                & (lat >= float(mask_spec["minimum_latitude_degrees_north"]))
                & (lat <= float(mask_spec["maximum_latitude_degrees_north"]))
                & (lon >= float(mask_spec["minimum_longitude_degrees_east"]))
                & (lon <= float(mask_spec["maximum_longitude_degrees_east"]))
                & (terrain <= float(mask_spec["maximum_source_terrain_m"]))
            )
            count = int(np.count_nonzero(mask))
            if count == 0:
                raise ValueError("frozen Plateau mask selects no forcing cells")
            if mask_count is None:
                mask_count = count
            elif count != mask_count:
                raise ValueError("frozen Plateau mask changes across forcing records")

            u10 = values(dataset, "U_10M", "u10m")
            v10 = values(dataset, "V_10M", "v10m")
            speed = np.hypot(u10, v10)
            median_u10 = float(np.nanmedian(speed[mask]))

            temperature = values(dataset, "T")
            pressure = values(dataset, "P")
            height = values(dataset, "HFL")
            if not (
                temperature.shape == pressure.shape == height.shape
                and temperature.ndim == 3
                and temperature.shape[1:] == mask.shape
            ):
                raise ValueError("T/P/HFL forcing dimensions are inconsistent")
            delta_height = height - height[0:1, :, :]
            upper_mask = delta_height >= 300.0
            has_upper = np.any(upper_mask, axis=0)
            if not np.all(has_upper[mask]):
                raise ValueError("some Plateau cells lack a mass level 300 m higher")
            upper_index = np.argmax(upper_mask, axis=0)
            yy, xx = np.indices(mask.shape)
            theta = temperature * np.power(100000.0 / pressure, KAPPA)
            theta_change = theta[upper_index, yy, xx] - theta[0, :, :]
            median_theta_change = float(np.nanmedian(theta_change[mask]))
            hourly.append(
                {
                    "valid_time": expected_time.strftime(TIME_FORMAT),
                    "plateau_cell_count": count,
                    "median_u10_m_s": median_u10,
                    "median_theta_change_k": median_theta_change,
                    "calm": median_u10 <= float(contract["calmness"]["maximum_m_s"]),
                    "stable": median_theta_change
                    > float(contract["stability"]["minimum_change_k_exclusive"]),
                    "forcing_file": str(path),
                    "forcing_sha256": sha256(path),
                }
            )

    required_fraction = float(contract["required_hour_fraction"])
    calm_fraction = sum(item["calm"] for item in hourly) / len(hourly)
    stable_fraction = sum(item["stable"] for item in hourly) / len(hourly)
    joint_fraction = sum(item["calm"] and item["stable"] for item in hourly) / len(
        hourly
    )
    checks = {
        "hour_count": len(hourly) == int(period["expected_hours"]),
        "calm_hour_fraction": calm_fraction >= required_fraction,
        "stable_hour_fraction": stable_fraction >= required_fraction,
        "joint_calm_stable_hour_fraction": joint_fraction >= required_fraction,
    }
    payload = {
        "schema_version": 1,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "decision": (
            "PLATEAU_CASE_CONFIRMED"
            if all(checks.values())
            else "REPLACE_PLATEAU_CASE_BEFORE_HICAR_ASSESSMENT"
        ),
        "checks": checks,
        "contract": str(contract_path),
        "contract_sha256": sha256(contract_path),
        "forcing_plan": str(plan_path),
        "forcing_plan_sha256": sha256(plan_path),
        "required_hour_fraction": required_fraction,
        "calm_hour_fraction": calm_fraction,
        "stable_hour_fraction": stable_fraction,
        "joint_calm_stable_hour_fraction": joint_fraction,
        "plateau_cell_count": mask_count,
        "hourly": hourly,
        "scope": "ICON REA-L-CH1 case selection only; HICAR output was not read",
    }
    if report_path.exists() or Path(f"{report_path}.ready").exists():
        raise ValueError(f"refusing to replace existing gate report: {report_path}")
    write_json_atomic(report_path, payload)
    Path(f"{report_path}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--forcing-plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    payload = assess(
        contract_path=args.contract.resolve(),
        plan_path=args.forcing_plan.resolve(),
        report_path=args.report.resolve(),
    )
    print(
        f"REA-L Plateau selection gate: {payload['status']} "
        f"joint_fraction={payload['joint_calm_stable_hour_fraction']:.3f}"
    )
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
