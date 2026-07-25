#!/usr/bin/env python3
"""Measure NetCDF compression on representative HICAR two-dimensional fields."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--work-file", type=Path, required=True)
    parser.add_argument("--compression-level", type=int, default=1)
    parser.add_argument(
        "--variables",
        default="precipitation,psfc",
        help="Comma-separated representative 2-D time-dependent fields.",
    )
    args = parser.parse_args()
    names = [name.strip() for name in args.variables.split(",") if name.strip()]
    args.work_file.parent.mkdir(parents=True, exist_ok=True)
    args.work_file.unlink(missing_ok=True)
    logical_bytes = 0
    variable_details = {}
    with netCDF4.Dataset(args.source) as source, netCDF4.Dataset(
        args.work_file, "w", format="NETCDF4"
    ) as target:
        required = {"time", "lat", "lon", *names}
        missing = sorted(required - set(source.variables))
        if missing:
            raise SystemExit(f"source lacks variables: {', '.join(missing)}")
        dimensions = set()
        for name in ("time", "lat", "lon", *names):
            dimensions.update(source.variables[name].dimensions)
        for name in dimensions:
            dimension = source.dimensions[name]
            target.createDimension(name, None if dimension.isunlimited() else len(dimension))
        for name in ("time", "lat", "lon", *names):
            source_variable = source.variables[name]
            compress = name in names
            target_variable = target.createVariable(
                name,
                source_variable.dtype,
                source_variable.dimensions,
                zlib=compress,
                complevel=args.compression_level if compress else 0,
                shuffle=compress,
            )
            target_variable.setncatts(
                {
                    attribute: source_variable.getncattr(attribute)
                    for attribute in source_variable.ncattrs()
                    if attribute != "_FillValue"
                }
            )
            values = source_variable[:]
            target_variable[:] = values
            bytes_count = int(np.asarray(values).nbytes)
            logical_bytes += bytes_count
            variable_details[name] = {
                "shape": list(source_variable.shape),
                "dtype": str(source_variable.dtype),
                "logical_bytes": bytes_count,
            }
    compressed_bytes = args.work_file.stat().st_size
    payload = {
        "status": "PASS",
        "source": str(args.source.resolve()),
        "sample_variables": names,
        "compression_level": args.compression_level,
        "logical_bytes_including_coordinates": logical_bytes,
        "compressed_file_bytes": compressed_bytes,
        "compressed_to_logical_ratio": compressed_bytes / logical_bytes,
        "variables": variable_details,
    }
    write_json_atomic(args.report, payload)
    args.work_file.unlink()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
