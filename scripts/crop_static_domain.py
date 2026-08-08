#!/usr/bin/env python3
"""Crop a HICAR static grid and rebuild its boundary terrain blend."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def boundary_weight(ny: int, nx: int, width_cells: float) -> np.ndarray:
    """Return a cosine interior weight based on distance to the crop edge."""
    if width_cells <= 0:
        raise ValueError("blend width must be positive")
    yy, xx = np.indices((ny, nx))
    distance = np.minimum.reduce((xx, nx - 1 - xx, yy, ny - 1 - yy)).astype(float)
    phase = np.clip(distance / width_cells, 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * phase)


def crop_static(
    source: Path,
    output: Path,
    *,
    x_start: int,
    x_stop: int,
    y_start: int,
    y_stop: int,
    blend_width_km: float,
) -> None:
    with Dataset(source) as src:
        nx = len(src.dimensions["x"])
        ny = len(src.dimensions["y"])
        if not (0 <= x_start < x_stop <= nx and 0 <= y_start < y_stop <= ny):
            raise ValueError("crop bounds must be nonempty and inside the source grid")
        dx = float(getattr(src, "hicar_dx_m"))
        width_cells = blend_width_km * 1000.0 / dx
        weight = boundary_weight(y_stop - y_start, x_stop - x_start, width_cells)

        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        if partial.exists():
            partial.unlink()
        with Dataset(partial, "w", format="NETCDF4") as dst:
            for name, dimension in src.dimensions.items():
                if name == "x":
                    size = x_stop - x_start
                elif name == "y":
                    size = y_stop - y_start
                else:
                    size = None if dimension.isunlimited() else len(dimension)
                dst.createDimension(name, size)

            for attr in src.ncattrs():
                dst.setncattr(attr, src.getncattr(attr))
            dst.setncattr("topography_blend_width_km", float(blend_width_km))
            dst.setncattr(
                "crop_source",
                f"{source}: x[{x_start}:{x_stop}], y[{y_start}:{y_stop}]",
            )

            for name, variable in src.variables.items():
                kwargs = {}
                if variable.ndim >= 2 and "y" in variable.dimensions and "x" in variable.dimensions:
                    kwargs = {"zlib": True, "complevel": 2, "shuffle": True}
                out = dst.createVariable(name, variable.dtype, variable.dimensions, **kwargs)
                out.setncatts({attr: variable.getncattr(attr) for attr in variable.ncattrs()})
                index = tuple(
                    slice(x_start, x_stop)
                    if dim == "x"
                    else slice(y_start, y_stop)
                    if dim == "y"
                    else slice(None)
                    for dim in variable.dimensions
                )
                out[:] = variable[index]

            high = np.asarray(dst.variables["topo_highres"][:], dtype=np.float64)
            driving = np.asarray(dst.variables["topo_driving"][:], dtype=np.float64)
            dst.variables["topo_blend_weight"][:] = weight.astype(np.float32)
            dst.variables["topo"][:] = ((1.0 - weight) * driving + weight * high).astype(
                np.float32
            )
        partial.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--x-start", required=True, type=int)
    parser.add_argument("--x-stop", required=True, type=int, help="exclusive upper bound")
    parser.add_argument("--y-start", required=True, type=int)
    parser.add_argument("--y-stop", required=True, type=int, help="exclusive upper bound")
    parser.add_argument("--blend-width-km", type=float, default=30.0)
    args = parser.parse_args()
    crop_static(
        args.source,
        args.output,
        x_start=args.x_start,
        x_stop=args.x_stop,
        y_start=args.y_start,
        y_stop=args.y_stop,
        blend_width_km=args.blend_width_km,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
