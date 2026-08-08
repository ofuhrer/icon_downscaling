#!/usr/bin/env python3
"""Sample native-grid REA-L surface GRIB at SwissMetNet sites."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path

import netCDF4
import numpy as np


CURRENT_FIELDS = ("PS", "T_2M", "TD_2M", "U_10M", "V_10M", "H_SNOW", "W_SNOW")


def metadata(field, key: str):
    try:
        return field.metadata(key)
    except Exception:
        return None


def valid_time(field) -> datetime:
    value = datetime.strptime(
        f"{int(metadata(field, 'validityDate')):08d}{int(metadata(field, 'validityTime')):04d}",
        "%Y%m%d%H%M",
    )
    return value.replace(tzinfo=timezone.utc)


def field_values(field) -> np.ndarray:
    return np.asarray(field.to_numpy(flatten=True), dtype=np.float64)


def read_fields(paths: list[Path]) -> list:
    import earthkit.data as ekd

    result = []
    for path in paths:
        result.extend(ekd.from_source("file", str(path)).to_fieldlist())
    return result


def read_sites(path: Path) -> list[dict[str, object]]:
    sites: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream, delimiter=";")
        header = [value.strip().lower() for value in next(reader)]
        positions = {name: header.index(name) for name in (
            "meas_site", "nat_abbr", "latitude", "longitude", "elev"
        )}
        for row in reader:
            if len(row) < len(header):
                continue
            try:
                site = {
                    "meas_site": row[positions["meas_site"]].strip(),
                    "abbreviation": row[positions["nat_abbr"]].strip(),
                    "latitude": float(row[positions["latitude"]]),
                    "longitude": float(row[positions["longitude"]]),
                    "elevation_m": float(row[positions["elev"]]),
                }
            except ValueError:
                continue
            key = f"{site['abbreviation']}:{site['meas_site']}"
            if key.strip(":"):
                sites[key] = site
    if not sites:
        raise ValueError("observation CSV contains no usable station metadata")
    return [dict(site, station_key=key) for key, site in sorted(sites.items())]


def extpar_cell(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = np.squeeze(np.ma.asarray(dataset[name][:]).filled(np.nan))
    if values.ndim != 1:
        raise ValueError(f"EXTPAR {name} is not a one-dimensional cell field")
    return np.asarray(values, dtype=np.float64)


def nearest_cells(
    latitude: np.ndarray, longitude: np.ndarray, sites: list[dict[str, object]]
) -> tuple[np.ndarray, np.ndarray]:
    indices = []
    distances = []
    for site in sites:
        site_latitude = float(site["latitude"])
        site_longitude = float(site["longitude"])
        latitude_scale = 110.57
        longitude_scale = 111.32 * math.cos(math.radians(site_latitude))
        distance_squared = ((latitude - site_latitude) * latitude_scale) ** 2 + (
            (longitude - site_longitude) * longitude_scale
        ) ** 2
        index = int(np.nanargmin(distance_squared))
        indices.append(index)
        distances.append(math.sqrt(float(distance_squared[index])))
    return np.asarray(indices, dtype=np.int64), np.asarray(distances)


def specific_humidity(dewpoint_k: np.ndarray, pressure_pa: np.ndarray) -> np.ndarray:
    dewpoint_c = dewpoint_k - 273.15
    vapor_pressure = 611.2 * np.exp(
        17.67 * dewpoint_c / np.maximum(dewpoint_k - 29.65, 1.0)
    )
    return 0.622 * vapor_pressure / (pressure_pa - 0.378 * vapor_pressure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-grib", type=Path, action="append", required=True)
    parser.add_argument("--precipitation-grib", type=Path, required=True)
    parser.add_argument("--icon-extpar", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    if end <= start:
        raise SystemExit("end must be after start")
    expected_times = []
    value = start
    while value <= end:
        expected_times.append(value)
        value += timedelta(hours=1)

    current: dict[datetime, dict[str, np.ndarray]] = {}
    for field in read_fields(args.current_grib):
        name = str(metadata(field, "shortName") or "").upper()
        if name in CURRENT_FIELDS:
            current.setdefault(valid_time(field), {})[name] = field_values(field)
    missing = {
        time.isoformat(): sorted(set(CURRENT_FIELDS) - set(current.get(time, {})))
        for time in expected_times
        if set(current.get(time, {})) != set(CURRENT_FIELDS)
    }
    if missing:
        raise ValueError(f"incomplete REA-L current fields: {missing}")

    accumulated: dict[datetime, np.ndarray] = {}
    for field in read_fields([args.precipitation_grib]):
        if str(metadata(field, "shortName") or "").upper() == "TOT_PREC":
            accumulated[valid_time(field)] = field_values(field)
    if any(time not in accumulated for time in expected_times):
        absent = [time.isoformat() for time in expected_times if time not in accumulated]
        raise ValueError(f"missing REA-L cumulative precipitation endpoints: {absent}")

    sites = read_sites(args.observations)
    with netCDF4.Dataset(args.icon_extpar) as extpar:
        latitude = np.degrees(extpar_cell(extpar, "clat"))
        longitude = np.degrees(extpar_cell(extpar, "clon"))
        terrain = extpar_cell(extpar, "topography_c")
    cell_count = latitude.size
    if terrain.size != cell_count or any(
        values.size != cell_count for fields in current.values() for values in fields.values()
    ):
        raise ValueError("REA-L surface fields and EXTPAR have different cell counts")
    indices, distances_km = nearest_cells(latitude, longitude, sites)
    if float(np.max(distances_km)) > 2.0:
        raise ValueError(f"maximum station-to-REA-L-cell distance is {np.max(distances_km):.3f} km")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.name}.partial.{os.getpid()}")
    columns = (
        "valid_time", "station_key", "source_cell", "source_distance_km",
        "source_terrain_m", "ta2m_ref", "psfc_ref", "hus2m_ref", "u10m_ref",
        "v10m_ref", "snow_height_ref", "precipitation_interval_ref",
    )
    try:
        with partial.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            previous_precipitation = None
            for time in expected_times:
                fields = current[time]
                precipitation = accumulated[time]
                if previous_precipitation is None:
                    interval = np.zeros_like(precipitation)
                else:
                    interval = precipitation - previous_precipitation
                    minimum = float(np.min(interval))
                    if minimum < -0.01:
                        raise ValueError(
                            f"cumulative precipitation decreases by {minimum:g} kg m-2 at {time}"
                        )
                    interval = np.maximum(interval, 0.0)
                previous_precipitation = precipitation
                humidity = specific_humidity(fields["TD_2M"], fields["PS"])
                for site_index, (site, cell) in enumerate(zip(sites, indices)):
                    writer.writerow({
                        "valid_time": time.isoformat().replace("+00:00", "Z"),
                        "station_key": site["station_key"],
                        "source_cell": int(cell),
                        "source_distance_km": float(distances_km[site_index]),
                        "source_terrain_m": float(terrain[cell]),
                        "ta2m_ref": float(fields["T_2M"][cell]),
                        "psfc_ref": float(fields["PS"][cell]),
                        "hus2m_ref": float(humidity[cell]),
                        "u10m_ref": float(fields["U_10M"][cell]),
                        "v10m_ref": float(fields["V_10M"][cell]),
                        "snow_height_ref": float(fields["H_SNOW"][cell]),
                        "precipitation_interval_ref": float(interval[cell]),
                    })
        os.replace(partial, args.output)
        Path(f"{args.output}.ready").touch()
    finally:
        partial.unlink(missing_ok=True)
    print(
        f"PASS records={len(expected_times) * len(sites)} sites={len(sites)} "
        f"max_distance_km={np.max(distances_km):.3f} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
