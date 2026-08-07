#!/usr/bin/env python3
"""Publish exact-grid forcing and an execution plan for the synthetic radiation gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np


VARIABLES_FULL = ("P", "QV", "T", "U", "V")
SOURCE_EPOCH = datetime(2020, 7, 1, tzinfo=timezone.utc)
TARGET_EPOCH = datetime(2020, 7, 20, tzinfo=timezone.utc)
START = TARGET_EPOCH + timedelta(hours=6, minutes=30)
END = TARGET_EPOCH + timedelta(hours=9, minutes=30)
SPLIT = TARGET_EPOCH + timedelta(hours=8)
FORCING_INTERVAL = timedelta(minutes=30)
OUTPUT_INTERVAL = timedelta(minutes=5)
BLOCKED_SECTOR = 22  # zero based: [88, 92) degrees
HORIZON_ELEVATION_DEG = 30.0


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def publish_ready(path: Path) -> None:
    temporary = Path(f"{path}.ready.partial.{os.getpid()}")
    temporary.write_text(f"sha256 {digest(path)}  {path.name}\n", encoding="utf-8")
    os.replace(temporary, Path(f"{path}.ready"))


def atomic_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        publish_ready(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def julian_day(when: datetime) -> float:
    return when.timestamp() / 86400.0 + 2440587.5


def hicar_solar_position(when: datetime, lat: float, lon: float, tzone: float = 0.0) -> tuple[float, float]:
    """Reproduce HICAR's NOAA-derived elevation/azimuth calculation."""
    degree = math.pi / 180.0
    century = (julian_day(when) - tzone / 24.0 - 2451545.0) / 36525.0
    mean_longitude = (
        280.46646 + century * (36000.76983 + century * 0.0003032)
    ) % 360.0
    mean_anomaly = 357.52911 + century * (35999.05029 - 0.0001537 * century)
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)
    equation_center = (
        math.sin(degree * mean_anomaly) * (1.914602 - century * (0.004817 + 0.000014 * century))
        + math.sin(degree * 2.0 * mean_anomaly) * (0.019993 - 0.000101 * century)
        + math.sin(degree * 3.0 * mean_anomaly) * 0.000289
    )
    apparent_longitude = (
        mean_longitude + equation_center - 0.00569
        - 0.00478 * math.sin(degree * (125.04 - 1934.136 * century))
    )
    mean_obliquity = 23.0 + (
        26.0
        + (21.448 - century * (46.815 + century * (0.00059 - century * 0.001813))) / 60.0
    ) / 60.0
    obliquity = mean_obliquity + 0.00256 * math.cos(
        degree * (125.04 - 1934.136 * century)
    )
    declination = math.degrees(
        math.asin(math.sin(degree * obliquity) * math.sin(degree * apparent_longitude))
    )
    y = math.tan(degree * obliquity / 2.0) ** 2
    equation_time = 4.0 * math.degrees(
        y * math.sin(2.0 * degree * mean_longitude)
        - 2.0 * eccentricity * math.sin(degree * mean_anomaly)
        + 4.0 * eccentricity * y * math.sin(degree * mean_anomaly) * math.cos(2.0 * degree * mean_longitude)
        - 0.5 * y * y * math.sin(4.0 * degree * mean_longitude)
        - 1.25 * eccentricity * eccentricity * math.sin(2.0 * degree * mean_anomaly)
    )
    day_fraction = (when.hour * 3600 + when.minute * 60 + when.second) / 86400.0
    solar_minutes = (day_fraction * 1440.0 + equation_time + 4.0 * lon - 60.0 * tzone) % 1440.0
    hour_angle = solar_minutes / 4.0 - 180.0
    zenith = math.degrees(
        math.acos(
            math.sin(degree * lat) * math.sin(degree * declination)
            + math.cos(degree * lat) * math.cos(degree * declination) * math.cos(degree * hour_angle)
        )
    )
    elevation = 90.0 - zenith
    if elevation > 85.0:
        refraction = 0.0
    elif elevation > 5.0:
        tangent = math.tan(degree * elevation)
        refraction = (58.1 / tangent - 0.07 / tangent**3 + 0.000086 / tangent**5) / 3600.0
    elif elevation > -0.757:
        refraction = (
            1735.0 + elevation * (-518.2 + elevation * (103.4 + elevation * (-12.79 + elevation * 0.711)))
        ) / 3600.0
    else:
        refraction = -20.772 / math.tan(degree * elevation) / 3600.0
    argument = (
        math.sin(degree * lat) * math.cos(degree * zenith) - math.sin(degree * declination)
    ) / (math.cos(degree * lat) * math.sin(degree * zenith))
    base = math.degrees(math.acos(max(-1.0, min(1.0, argument))))
    azimuth = (base + 180.0) % 360.0 if hour_angle > 0.0 else (540.0 - base) % 360.0
    # The Fortran implementation truncates the azimuth to five decimal places.
    azimuth = math.floor(azimuth * 100000.0) / 100000.0
    return max(0.0, elevation + refraction), azimuth


def timeline(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    count = (end - start) / step
    if count != int(count):
        raise ValueError("time range is not divisible by interval")
    return [start + index * step for index in range(int(count) + 1)]


def source_path(source_dir: Path, when: datetime) -> Path:
    return source_dir / f"rea_l_hicar_{when:%Y%m%d_%H%M}.nc"


def require_published(path: Path) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"input is not published: {path}")


def source_column(path: Path, iy: int, ix: int) -> dict[str, np.ndarray | float]:
    with netCDF4.Dataset(path) as dataset:
        result: dict[str, np.ndarray | float] = {
            name: np.asarray(dataset.variables[name][0, :, iy, ix], dtype=np.float64)
            for name in VARIABLES_FULL
        }
        result["W"] = np.asarray(dataset.variables["W"][0, :, iy, ix], dtype=np.float64)
        result["HFL"] = np.asarray(dataset.variables["HFL"][:, iy, ix], dtype=np.float64)
        result["HHL"] = np.asarray(dataset.variables["HHL"][:, iy, ix], dtype=np.float64)
        result["HSURF"] = float(dataset.variables["HSURF"][iy, ix])
    return result


def interpolate(left: dict, right: dict, fraction: float) -> dict:
    return {
        name: np.asarray(left[name]) * (1.0 - fraction) + np.asarray(right[name]) * fraction
        for name in (*VARIABLES_FULL, "W")
    }


def write_forcing(
    path: Path,
    when: datetime,
    lat: np.ndarray,
    lon: np.ndarray,
    state: dict,
    geometry: dict,
) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    ny, nx = lat.shape
    try:
        with netCDF4.Dataset(temporary_path, "w", format="NETCDF4") as dataset:
            dataset.createDimension("y_1", ny)
            dataset.createDimension("x_1", nx)
            dataset.createDimension("z", len(geometry["HFL"]))
            dataset.createDimension("z_hl", len(geometry["HHL"]))
            dataset.createDimension("time", None)
            dataset.createVariable("lat_1", "f4", ("y_1", "x_1"), zlib=True)[:] = lat
            dataset.createVariable("lon_1", "f4", ("y_1", "x_1"), zlib=True)[:] = lon
            dataset.variables["lat_1"].units = "degrees_north"
            dataset.variables["lon_1"].units = "degrees_east"
            dataset.createVariable("x_1", "f4", ("x_1",))[:] = np.arange(nx)
            dataset.createVariable("y_1", "f4", ("y_1",))[:] = np.arange(ny)
            dataset.createVariable("z", "f4", ("z",))[:] = np.arange(len(geometry["HFL"]))
            dataset.createVariable("z_hl", "f4", ("z_hl",))[:] = np.arange(len(geometry["HHL"]))
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = f"minutes since {TARGET_EPOCH:%Y-%m-%d %H:%M:%S}"
            time.calendar = "gregorian"
            time[:] = [(when - TARGET_EPOCH).total_seconds() / 60.0]
            dataset.createVariable("FR_LAND", "f4", ("y_1", "x_1"), zlib=True)[:] = 1.0
            dataset.createVariable("HSURF", "f4", ("y_1", "x_1"), zlib=True)[:] = 0.0
            for name in ("HFL", "HHL"):
                zdim = "z" if name == "HFL" else "z_hl"
                variable = dataset.createVariable(name, "f4", (zdim, "y_1", "x_1"), zlib=True)
                variable[:] = np.broadcast_to(np.asarray(geometry[name])[:, None, None], variable.shape)
                variable.units = "m"
            for name in VARIABLES_FULL:
                variable = dataset.createVariable(name, "f4", ("time", "z", "y_1", "x_1"), zlib=True)
                variable[:] = np.broadcast_to(np.asarray(state[name])[None, :, None, None], variable.shape)
            vertical = dataset.createVariable("W", "f4", ("time", "z_hl", "y_1", "x_1"), zlib=True)
            vertical[:] = np.broadcast_to(np.asarray(state["W"])[None, :, None, None], vertical.shape)
            for name, units in {"P": "Pa", "T": "K", "QV": "1", "U": "m s-1", "V": "m s-1", "W": "m s-1"}.items():
                dataset.variables[name].units = units
            dataset.Conventions = "CF-1.8"
            dataset.source = "ICON REA-L-CH1 single-column synthetic causal forcing"
            dataset.qualification_scope = "terrain-radiation component and restart gate only"
            dataset.vertical_adjustment = (
                "HFL/HHL shifted by source HSURF; pressure multiplied by one hydrostatic surface factor"
            )
        os.replace(temporary_path, path)
        publish_ready(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def prepare(static_file: Path, source_dir: Path, output_dir: Path) -> dict:
    require_published(static_file)
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite output directory: {output_dir}")
    output_dir.mkdir(parents=True)
    forcing_dir = output_dir / "forcing"
    forcing_dir.mkdir()
    with netCDF4.Dataset(static_file) as static:
        lat = np.asarray(static.variables["lat"][:], dtype=np.float64)
        lon = np.asarray(static.variables["lon"][:], dtype=np.float64)
    center_lat = float(lat[lat.shape[0] // 2, lat.shape[1] // 2])
    center_lon = float(lon[lon.shape[0] // 2, lon.shape[1] // 2])

    source_times = timeline(SOURCE_EPOCH + timedelta(hours=6), SOURCE_EPOCH + timedelta(hours=10), timedelta(hours=1))
    sources = [source_path(source_dir, when) for when in source_times]
    for path in sources:
        require_published(path)
    with netCDF4.Dataset(sources[0]) as source:
        slat = np.asarray(source.variables["lat_1"][:], dtype=np.float64)
        slon = np.asarray(source.variables["lon_1"][:], dtype=np.float64)
    distance = (slat - center_lat) ** 2 + ((slon - center_lon) * math.cos(math.radians(center_lat))) ** 2
    iy, ix = np.unravel_index(int(np.argmin(distance)), distance.shape)
    columns = {when: source_column(path, iy, ix) for when, path in zip(source_times, sources)}
    first = columns[source_times[0]]
    source_surface = float(first["HSURF"])
    lowest_full_level = int(np.argmin(np.asarray(first["HFL"])))
    pressure_factor = math.exp(
        9.80665 * source_surface
        / (287.05 * float(np.asarray(first["T"])[lowest_full_level]))
    )
    geometry = {
        "HFL": np.asarray(first["HFL"]) - source_surface,
        "HHL": np.asarray(first["HHL"]) - source_surface,
    }
    if not np.all(np.isfinite(geometry["HFL"])) or not np.all(np.isfinite(geometry["HHL"])):
        raise ValueError("source vertical geometry is non-finite")

    artifacts = []
    target_times = timeline(START, END, FORCING_INTERVAL)
    for target in target_times:
        source_when = SOURCE_EPOCH + (target - TARGET_EPOCH)
        left = source_when.replace(minute=0, second=0, microsecond=0)
        right = left + timedelta(hours=1)
        fraction = (source_when - left) / timedelta(hours=1)
        state = interpolate(columns[left], columns[right], float(fraction))
        state["P"] *= pressure_factor
        path = forcing_dir / f"terrain_gate_{target:%Y%m%d_%H%M}.nc"
        write_forcing(path, target, lat, lon, state, geometry)
        artifacts.append({"valid_time": target.isoformat(), "path": str(path), "sha256": digest(path), "size_bytes": path.stat().st_size})

    solar = []
    for when in timeline(START, END, OUTPUT_INTERVAL):
        elevation, azimuth = hicar_solar_position(when, center_lat, center_lon)
        sector = min(int(math.floor(azimuth / 4.0)), 89)
        solar.append({
            "valid_time": when.isoformat(),
            "elevation_degrees": elevation,
            "azimuth_degrees": azimuth,
            "zero_based_sector": sector,
            "visible_in_blocked_sector": sector != BLOCKED_SECTOR or elevation >= HORIZON_ELEVATION_DEG,
        })
    blocked = [sample for sample in solar if sample["zero_based_sector"] == BLOCKED_SECTOR]
    if not blocked or not any(not sample["visible_in_blocked_sector"] for sample in blocked) or not any(sample["visible_in_blocked_sector"] for sample in blocked):
        raise ValueError("selected solar path does not sample both shadowed and visible states in blocked sector")

    list_paths = {}
    selections = {
        "continuous": target_times,
        "split_first": [when for when in target_times if when <= SPLIT],
        "split_second": [when for when in target_times if when >= SPLIT],
    }
    by_time = {datetime.fromisoformat(item["valid_time"]): Path(item["path"]) for item in artifacts}
    for label, times in selections.items():
        path = output_dir / f"forcing_{label}.txt"
        path.write_text("".join(f'"{by_time[when]}"\n' for when in times), encoding="utf-8")
        publish_ready(path)
        list_paths[label] = {"path": str(path), "sha256": digest(path)}

    plan = {
        "schema": "hicar-terrain-radiation-model-gate/v1",
        "scope": "synthetic_component_and_restart_qualification_only",
        "static": {"path": str(static_file), "sha256": digest(static_file)},
        "source_column": {
            "grid_index_yx": [int(iy), int(ix)],
            "latitude": float(slat[iy, ix]),
            "longitude": float(slon[iy, ix]),
            "surface_height_m": source_surface,
            "pressure_factor": pressure_factor,
            "files": [{"path": str(path), "sha256": digest(path), "size_bytes": path.stat().st_size} for path in sources],
        },
        "timeline": {
            "start": START.isoformat(), "split": SPLIT.isoformat(), "end": END.isoformat(),
            "forcing_interval_seconds": int(FORCING_INTERVAL.total_seconds()),
            "output_interval_seconds": int(OUTPUT_INTERVAL.total_seconds()),
            "radiation_update_interval_seconds": int(OUTPUT_INTERVAL.total_seconds()),
        },
        "forcing": artifacts,
        "forcing_lists": list_paths,
        "solar_path": solar,
        "blocked_sector_samples": blocked,
        "experiment_matrix": [
            {"case": "flat_off", "static": "flat", "profile": "off"},
            {"case": "flat_direct", "static": "flat", "profile": "direct"},
            {"case": "flat_direct_diffuse", "static": "flat", "profile": "direct-diffuse"},
            {"case": "blocked_direct", "static": "blocked", "profile": "direct"},
            {"case": "blocked_direct_diffuse", "static": "blocked", "profile": "direct-diffuse"},
        ],
        "restart": {"split_at": SPLIT.isoformat(), "comparison_interval_seconds": 300},
        "promotion_limit": "Does not qualify national terrain radiation or climatological skill.",
    }
    atomic_json(output_dir / "execution_plan.json", plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-file", required=True, type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        plan = prepare(args.static_file.resolve(), args.source_dir.resolve(), args.output_dir.resolve())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": "PASS", "forcing_records": len(plan["forcing"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
