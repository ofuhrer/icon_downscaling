#!/usr/bin/env python3
"""Benchmark sparse-LBC NetCDF encodings from one materialized Swiss frame."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import netCDF4
import numpy as np


FLOAT_FIELDS = ("T", "P", "QV", "QC", "QI", "HFL", "HHL")


def _load(path: Path) -> tuple[dict[str, np.ndarray], dict[str, object], dict[str, dict[str, object]]]:
    with netCDF4.Dataset(path) as source:
        arrays = {name: np.asarray(source[name][:]) for name in source.variables}
        attributes = {name: source.getncattr(name) for name in source.ncattrs()}
        variable_attributes = {
            name: {attribute: variable.getncattr(attribute) for attribute in variable.ncattrs()}
            for name, variable in source.variables.items()
        }
    return arrays, attributes, variable_attributes


def _write(
    path: Path,
    arrays: dict[str, np.ndarray],
    attributes: dict[str, object],
    variable_attributes: dict[str, dict[str, object]],
    *,
    field_dtype: str,
    compression_level: int,
    point_chunk: int | None,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    started = time.perf_counter()
    try:
        with netCDF4.Dataset(temporary, "w") as target:
            levels, points = arrays["T"].shape
            half_levels = arrays["HHL"].shape[0]
            target.createDimension("boundary_point", points)
            target.createDimension("level", levels)
            target.createDimension("half_level", half_levels)
            target.createVariable("row", "i4", ("boundary_point",))[:] = arrays["row"]
            target.createVariable("column", "i4", ("boundary_point",))[:] = arrays["column"]
            weight = target.createVariable("relaxation_weight", "f8", ("boundary_point",))
            weight[:] = arrays["relaxation_weight"]
            for name in FLOAT_FIELDS:
                dimensions = (
                    ("half_level", "boundary_point")
                    if name == "HHL"
                    else ("level", "boundary_point")
                )
                kwargs: dict[str, object] = {}
                if compression_level > 0:
                    kwargs.update(zlib=True, complevel=compression_level, shuffle=True)
                if point_chunk is not None:
                    kwargs["chunksizes"] = (
                        arrays[name].shape[0],
                        min(point_chunk, points),
                    )
                variable = target.createVariable(name, field_dtype, dimensions, **kwargs)
                variable[:] = arrays[name]
                for attribute, value in variable_attributes[name].items():
                    variable.setncattr(attribute, value)
            for name in ("row", "column", "relaxation_weight"):
                for attribute, value in variable_attributes[name].items():
                    target[name].setncattr(attribute, value)
            for attribute, value in attributes.items():
                target.setncattr(attribute, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    elapsed = time.perf_counter() - started
    with netCDF4.Dataset(path) as product:
        layout = {
            name: {
                "dtype": str(product[name].dtype),
                "chunking": product[name].chunking(),
                "filters": product[name].filters(),
            }
            for name in FLOAT_FIELDS
        }
    return {
        "path": str(path),
        "seconds": elapsed,
        "bytes": path.stat().st_size,
        "field_dtype": field_dtype,
        "compression_level": compression_level,
        "point_chunk": point_chunk,
        "layout": layout,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    load_started = time.perf_counter()
    arrays, attributes, variable_attributes = _load(args.reference)
    load_seconds = time.perf_counter() - load_started
    candidates = (
        ("f8_zlib4_auto", "f8", 4, None),
        ("f8_zlib1_auto", "f8", 1, None),
        ("f4_zlib4_auto", "f4", 4, None),
        ("f4_zlib1_auto", "f4", 1, None),
        ("f4_zlib1_p512", "f4", 1, 512),
        ("f4_zlib1_p2048", "f4", 1, 2048),
        ("f4_zlib1_p4096", "f4", 1, 4096),
        ("f4_zlib1_p8192", "f4", 1, 8192),
        ("f4_zlib1_p16384", "f4", 1, 16384),
        ("f4_uncompressed", "f4", 0, None),
    )
    results = []
    for label, dtype, level, point_chunk in candidates:
        result = _write(
            args.output_dir / f"boundary_{label}.nc",
            arrays,
            attributes,
            variable_attributes,
            field_dtype=dtype,
            compression_level=level,
            point_chunk=point_chunk,
        )
        result["label"] = label
        results.append(result)
        print(json.dumps({key: result[key] for key in ("label", "seconds", "bytes")}))
    payload = {
        "schema": "hicarprep-boundary-encoding-benchmark-v1",
        "reference": str(args.reference),
        "reference_bytes": args.reference.stat().st_size,
        "load_seconds": load_seconds,
        "shape": list(arrays["T"].shape),
        "candidates": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_name(f".{args.report.name}.{os.getpid()}.partial")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
