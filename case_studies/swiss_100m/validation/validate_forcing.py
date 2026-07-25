#!/usr/bin/env python3
"""Validate a structured HICAR forcing record before it is published."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np


REQUIRED = ("P", "QV", "T", "U", "V", "W", "HFL", "HHL", "HSURF", "FR_LAND")


def coord_var(ds, choices):
    for name in choices:
        if name in ds.variables:
            return ds.variables[name]
    raise SystemExit("missing coordinate; expected one of: " + ", ".join(choices))


def edge_bounds(ds):
    lat = coord_var(ds, ("lat", "lat_1"))
    lon = coord_var(ds, ("lon", "lon_1"))
    if lat.ndim == 1 and lon.ndim == 1:
        return float(np.min(lat[:])), float(np.max(lat[:])), float(np.min(lon[:])), float(np.max(lon[:]))
    lat_edges = np.concatenate((lat[0, :], lat[-1, :], lat[:, 0], lat[:, -1]))
    lon_edges = np.concatenate((lon[0, :], lon[-1, :], lon[:, 0], lon[:, -1]))
    return float(np.min(lat_edges)), float(np.max(lat_edges)), float(np.min(lon_edges)), float(np.max(lon_edges))


def finite_range(var):
    values = np.ma.asarray(var[:]).filled(np.nan)
    if not np.isfinite(values).all():
        raise SystemExit(f"{var.name}: contains NaN or Inf")
    return float(values.min()), float(values.max())


def forcing_time(ds):
    if "time" not in ds.variables:
        raise SystemExit("forcing is missing time")
    variable = ds.variables["time"]
    if variable.size != 1:
        raise SystemExit(f"forcing must contain exactly one time record, found {variable.size}")
    units = getattr(variable, "units", "")
    if not units:
        raise SystemExit("forcing time variable has no units")
    value = netCDF4.num2date(
        variable[0],
        units,
        calendar=getattr(variable, "calendar", "standard"),
    )
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forcing-file", required=True, type=Path)
    parser.add_argument(
        "--published-forcing-file",
        type=Path,
        help="Path to record in the report when validating a pre-publication partial.",
    )
    parser.add_argument("--static-file", required=True, type=Path)
    parser.add_argument(
        "--expected-valid-time",
        help="Require the single forcing record to match this ISO timestamp.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    with netCDF4.Dataset(args.forcing_file) as forcing, netCDF4.Dataset(args.static_file) as static:
        missing = [name for name in REQUIRED if name not in forcing.variables]
        if missing:
            raise SystemExit("missing forcing variables: " + ", ".join(missing))
        for dimension in (("x", "x_1"), ("y", "y_1"), ("z",), ("z_hl",)):
            if not any(name in forcing.dimensions for name in dimension):
                raise SystemExit("missing forcing dimension: " + " or ".join(dimension))
        if len(forcing.dimensions["z_hl"]) != len(forcing.dimensions["z"]) + 1:
            raise SystemExit("forcing z_hl must have exactly one more level than z")
        valid_time = forcing_time(forcing)
        if args.expected_valid_time:
            try:
                expected_valid_time = datetime.fromisoformat(
                    args.expected_valid_time.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError as exc:
                raise SystemExit("--expected-valid-time must be an ISO timestamp") from exc
            if valid_time != expected_valid_time:
                raise SystemExit(
                    f"forcing valid time {valid_time.isoformat()} does not match "
                    f"{expected_valid_time.isoformat()}"
                )

        ranges = {name: finite_range(forcing.variables[name]) for name in REQUIRED}
        if not (100.0 <= ranges["P"][0] and ranges["P"][1] <= 120000.0):
            raise SystemExit(f"P range is implausible: {ranges['P']}")
        if not (150.0 <= ranges["T"][0] and ranges["T"][1] <= 350.0):
            raise SystemExit(f"T range is implausible: {ranges['T']}")
        if not (0.0 <= ranges["QV"][0] and ranges["QV"][1] <= 0.1):
            raise SystemExit(f"QV range is implausible: {ranges['QV']}")
        if not (-200.0 <= ranges["U"][0] <= ranges["U"][1] <= 200.0 and -200.0 <= ranges["V"][0] <= ranges["V"][1] <= 200.0):
            raise SystemExit("wind range is implausible")
        if not (0.0 <= ranges["FR_LAND"][0] <= ranges["FR_LAND"][1] <= 1.0):
            raise SystemExit(f"FR_LAND range is implausible: {ranges['FR_LAND']}")

        hhl_var = forcing.variables["HHL"]
        hhl = np.ma.asarray(hhl_var[:]).filled(np.nan)
        if np.any(np.diff(hhl, axis=hhl_var.dimensions.index("z_hl")) <= 0.0):
            raise SystemExit("HHL is not strictly bottom-to-top")
        hfl_var = forcing.variables["HFL"]
        hfl = np.ma.asarray(hfl_var[:]).filled(np.nan)
        if np.any(np.diff(hfl, axis=hfl_var.dimensions.index("z")) <= 0.0):
            raise SystemExit("HFL is not strictly bottom-to-top")
        expected_hfl = 0.5 * (
            np.take(hhl, indices=range(hhl.shape[hhl_var.dimensions.index("z_hl")] - 1),
                    axis=hhl_var.dimensions.index("z_hl"))
            + np.take(hhl, indices=range(1, hhl.shape[hhl_var.dimensions.index("z_hl")]),
                      axis=hhl_var.dimensions.index("z_hl"))
        )
        if not np.allclose(hfl, expected_hfl, rtol=2.0e-6, atol=2.0e-3):
            maximum_error = float(np.max(np.abs(hfl - expected_hfl)))
            raise SystemExit(f"HFL is inconsistent with adjacent HHL averaging: {maximum_error}")

        forcing_bounds = edge_bounds(forcing)
        static_bounds = edge_bounds(static)
        if forcing_bounds[0] > static_bounds[0] or forcing_bounds[1] < static_bounds[1] or \
           forcing_bounds[2] > static_bounds[2] or forcing_bounds[3] < static_bounds[3]:
            raise SystemExit(f"forcing does not cover static domain: forcing={forcing_bounds}, static={static_bounds}")
        report = {
            "status": "PASS",
            "forcing_file": str(args.published_forcing_file or args.forcing_file),
            "valid_time": valid_time.isoformat(),
            "forcing_dimensions": {name: len(dim) for name, dim in forcing.dimensions.items()},
            "forcing_bounds_latlon": forcing_bounds,
            "static_bounds_latlon": static_bounds,
            "ranges": ranges,
        }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        write_atomic(args.report, text)
    print(text, end="")


if __name__ == "__main__":
    main()
