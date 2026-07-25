#!/usr/bin/env python3
"""Publish public MeteoSwiss gridded references from the OGD STAC archive."""

from __future__ import annotations

import argparse
import calendar
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import time
import urllib.request

import netCDF4
import numpy as np


STAC_ROOT = "https://data.geo.admin.ch/api/stac/v1/collections"
SURFACE_COLLECTION = "ch.meteoschweiz.ogd-surface-derived-grid"
SATELLITE_COLLECTION = "ch.meteoschweiz.ogd-satellite-derived-grid"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "ICON-HICAR-scientific-validation/1"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def archive_item(collection: str) -> dict:
    return fetch_json(f"{STAC_ROOT}/{collection}/items/archive-ch")


def select_one(assets: dict, pattern: str) -> tuple[str, dict]:
    matches = [
        (key, value)
        for key, value in assets.items()
        if re.search(pattern, key)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one OGD asset for {pattern!r}, found "
            f"{[key for key, _ in matches]}"
        )
    return matches[0]


def selected_assets(
    year: int,
    months: list[int],
    surface_item: dict,
    satellite_item: dict,
) -> list[dict]:
    surface_assets = surface_item["assets"]
    satellite_assets = satellite_item["assets"]
    selected = []
    year_start = f"{year:04d}0101000000"
    year_end = f"{year:04d}1231000000"
    for product in ("rhiresd", "tabsd"):
        key, asset = select_one(
            surface_assets,
            rf"\.{product}_.*_{year_start}_{year_end}\.nc$",
        )
        selected.append(
            {
                "product": product,
                "year": year,
                "temporal_resolution": "daily",
                "collection": SURFACE_COLLECTION,
                "item": "archive-ch",
                "asset_key": key,
                "url": asset["href"],
                "media_type": asset.get("type"),
                "minimum_time_records": 365,
            }
        )
    for month in sorted(set(months)):
        if not 1 <= month <= 12:
            raise ValueError(f"month must be 1..12, got {month}")
        last_day = calendar.monthrange(year, month)[1]
        start = f"{year:04d}{month:02d}01000000"
        end = f"{year:04d}{month:02d}{last_day:02d}230000"
        for product in ("sis", "sis-no-horizon"):
            key, asset = select_one(
                satellite_assets,
                rf"\.msg\.{product}\.h_.*_{start}_{end}\.nc$",
            )
            selected.append(
                {
                    "product": product,
                    "year": year,
                    "temporal_resolution": "hourly",
                    "collection": SATELLITE_COLLECTION,
                    "item": "archive-ch",
                    "asset_key": key,
                    "url": asset["href"],
                    "media_type": asset.get("type"),
                    "minimum_time_records": 24 * last_day,
                    "month": month,
                }
            )
    return selected


def year_months_for_period(
    start: date,
    end_exclusive: date,
) -> dict[int, list[int]]:
    if end_exclusive <= start:
        raise ValueError("period end must be later than period start")
    selected: dict[int, list[int]] = {}
    current = date(start.year, start.month, 1)
    while current < end_exclusive:
        selected.setdefault(current.year, []).append(current.month)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return selected


def selected_period_assets(
    year_months: dict[int, list[int]],
    surface_item: dict,
    satellite_item: dict,
) -> list[dict]:
    selected = []
    for year, months in sorted(year_months.items()):
        selected.extend(
            selected_assets(
                year,
                months,
                surface_item,
                satellite_item,
            )
        )
    return selected


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def time_variable(dataset: netCDF4.Dataset) -> netCDF4.Variable:
    candidates = []
    for name, variable in dataset.variables.items():
        if (
            name.lower() == "time"
            or getattr(variable, "standard_name", "").lower() == "time"
            or getattr(variable, "axis", "").upper() == "T"
        ):
            candidates.append(variable)
    if len(candidates) != 1:
        raise ValueError(
            f"expected one time coordinate, found "
            f"{[variable.name for variable in candidates]}"
        )
    return candidates[0]


def inspect_netcdf(path: Path, minimum_time_records: int) -> dict:
    with netCDF4.Dataset(path) as dataset:
        time_coordinate = time_variable(dataset)
        if len(time_coordinate.dimensions) != 1:
            raise ValueError("time coordinate is not one-dimensional")
        time_dimension = time_coordinate.dimensions[0]
        record_count = len(dataset.dimensions[time_dimension])
        if record_count < minimum_time_records:
            raise ValueError(
                f"{path.name} has {record_count} records; "
                f"expected at least {minimum_time_records}"
            )
        data_variables = [
            variable
            for variable in dataset.variables.values()
            if time_dimension in variable.dimensions
            and variable.name != time_coordinate.name
            and np.issubdtype(variable.dtype, np.number)
        ]
        if not data_variables:
            raise ValueError("no numeric time-dependent data variable found")
        variable = max(data_variables, key=lambda value: value.ndim)
        time_axis = variable.dimensions.index(time_dimension)
        samples = []
        for index in (0, record_count - 1):
            selection = [slice(None)] * variable.ndim
            selection[time_axis] = index
            values = np.ma.asarray(variable[tuple(selection)])
            if np.ma.count(values) == 0:
                raise ValueError(
                    f"{variable.name} record {index} has no unmasked values"
                )
            finite = np.asarray(values.compressed(), dtype=np.float64)
            finite = finite[np.isfinite(finite)]
            if not finite.size:
                raise ValueError(
                    f"{variable.name} record {index} has no finite values"
                )
            samples.append(
                {
                    "record_index": index,
                    "finite_count": int(finite.size),
                    "minimum": float(np.min(finite)),
                    "maximum": float(np.max(finite)),
                }
            )
        decoded = None
        if hasattr(time_coordinate, "units"):
            values = netCDF4.num2date(
                time_coordinate[[0, record_count - 1]],
                units=time_coordinate.units,
                calendar=getattr(time_coordinate, "calendar", "standard"),
            )
            decoded = [str(value) for value in values]
        return {
            "dimensions": {
                name: len(dimension)
                for name, dimension in dataset.dimensions.items()
            },
            "variables": sorted(dataset.variables),
            "time_coordinate": time_coordinate.name,
            "time_record_count": record_count,
            "decoded_time_bounds": decoded,
            "sampled_data_variable": variable.name,
            "endpoint_samples": samples,
            "global_attributes": {
                name: str(getattr(dataset, name))
                for name in dataset.ncattrs()
                if name
                in {
                    "title",
                    "institution",
                    "source",
                    "history",
                    "Conventions",
                    "references",
                }
            },
        }


def download(url: str, target: Path, retries: int = 3) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "ICON-HICAR-scientific-validation/1"}
    )
    last_error = None
    for attempt in range(1, retries + 1):
        partial = target.with_name(f".{target.name}.partial.{os.getpid()}")
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                with partial.open("wb") as stream:
                    while True:
                        block = response.read(8 * 1024 * 1024)
                        if not block:
                            break
                        stream.write(block)
            if not partial.stat().st_size:
                raise ValueError(f"download is empty: {url}")
            os.replace(partial, target)
            return
        except Exception as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed after {retries} attempts: {url}") from last_error


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int, action="append")
    parser.add_argument("--period-start")
    parser.add_argument("--period-end-exclusive")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    period_mode = args.period_start is not None or args.period_end_exclusive is not None
    legacy_mode = args.year is not None or args.month is not None
    if period_mode and legacy_mode:
        raise SystemExit(
            "use either --year/--month or --period-start/--period-end-exclusive"
        )
    if period_mode:
        if args.period_start is None or args.period_end_exclusive is None:
            raise SystemExit(
                "period mode requires --period-start and --period-end-exclusive"
            )
        period_start = date.fromisoformat(args.period_start)
        period_end = date.fromisoformat(args.period_end_exclusive)
        year_months = year_months_for_period(period_start, period_end)
    else:
        if args.year is None or not args.month:
            raise SystemExit("legacy mode requires --year and at least one --month")
        period_start = None
        period_end = None
        year_months = {args.year: sorted(set(args.month))}

    if args.manifest.is_file() and Path(f"{args.manifest}.ready").is_file():
        published = json.loads(args.manifest.read_text())
        published_year_months = published.get("year_months")
        if published_year_months is None and published.get("year") is not None:
            published_year_months = {
                str(published["year"]): published.get("months", [])
            }
        requested_year_months = {
            str(year): sorted(set(months))
            for year, months in sorted(year_months.items())
        }
        if published_year_months != requested_year_months:
            raise SystemExit(
                "published OGD manifest covers a different year/month selection"
            )
        for item in published["assets"]:
            path = Path(item["path"])
            if not path.is_file() or sha256(path) != item["sha256"]:
                raise SystemExit(f"published OGD asset changed: {path}")
        print(f"OGD reference publication already verified: {args.manifest}")
        return 0
    if args.manifest.exists() or Path(f"{args.manifest}.ready").exists():
        raise SystemExit(
            f"incomplete OGD manifest publication requires review: {args.manifest}"
        )

    surface = archive_item(SURFACE_COLLECTION)
    satellite = archive_item(SATELLITE_COLLECTION)
    assets = selected_period_assets(year_months, surface, satellite)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    published_assets = []
    for asset in assets:
        path = args.output_dir / asset["asset_key"]
        ready = Path(f"{path}.ready")
        if path.is_file() and ready.is_file():
            pass
        elif path.exists() or ready.exists():
            raise SystemExit(f"incomplete OGD asset publication requires review: {path}")
        else:
            download(asset["url"], path)
        inspection = inspect_netcdf(path, asset["minimum_time_records"])
        if not ready.is_file():
            ready.touch()
        published_assets.append(
            {
                **asset,
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "inspection": inspection,
            }
        )

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "source": "MeteoSwiss Open Government Data STAC archive",
        "stac_api": STAC_ROOT,
        "period": {
            "start": period_start.isoformat() if period_start else None,
            "end_exclusive": period_end.isoformat() if period_end else None,
        },
        "years": sorted(year_months),
        "year_months": {
            str(year): sorted(set(months))
            for year, months in sorted(year_months.items())
        },
        "year": next(iter(year_months)) if len(year_months) == 1 else None,
        "months": (
            sorted(set(next(iter(year_months.values()))))
            if len(year_months) == 1
            else None
        ),
        "semantics": {
            "rhiresd": (
                "Daily precipitation on day D is the water-equivalent "
                "accumulation from 06 UTC D to 06 UTC D+1."
            ),
            "tabsd": "Daily mean two-metre temperature from 00 to 24 UTC.",
            "sis": "Hourly surface incoming shortwave radiation with terrain horizon.",
            "sis-no-horizon": (
                "Hourly surface incoming shortwave radiation without terrain horizon."
            ),
        },
        "assets": published_assets,
    }
    write_json_atomic(args.manifest, payload)
    Path(f"{args.manifest}.ready").touch()
    print(
        f"PASS: published {len(published_assets)} OGD grid references at "
        f"{args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
