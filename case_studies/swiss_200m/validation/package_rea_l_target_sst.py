#!/usr/bin/env python3
"""Remap one exact-valid-time REA-L skin temperature to HICAR water cells."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import netCDF4
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preprocessing.hicarprep.remap import RBFWeights, coordinates_in_degrees  # noqa: E402
from preprocessing.hicarprep.sst import build_target_sst_product  # noqa: E402

from package_rea_l_native_surface import (  # noqa: E402
    _cell_variable,
    _grib_valid_time,
    _iso_utc,
    _normalized_units,
    grid_spec,
    metadata,
    normalized_uuid,
    read_grib_fields,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-grib", type=Path, required=True)
    parser.add_argument("--icon-extpar", type=Path, required=True)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--valid-time", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.surface_grib, args.icon_extpar, args.static, args.weights):
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"missing or empty input: {path}")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")

    fields = read_grib_fields(args.surface_grib)
    if len(fields) != 1:
        raise ValueError(f"SST GRIB requires exactly one SKT message, found {len(fields)}")
    field = fields[0]
    if str(metadata(field, "shortName") or "").upper() != "SKT":
        raise ValueError("SST GRIB message is not SKT")
    units = metadata(field, "units")
    if _normalized_units(units) != "k":
        raise ValueError(f"SKT units {units!r} are not kelvin")
    valid_time = _iso_utc(args.valid_time)
    if _grib_valid_time(field) != valid_time:
        raise ValueError("SKT valid time differs from the requested forcing time")
    if str(metadata(field, "stepType") or "").lower() != "instant":
        raise ValueError("SKT must be an instantaneous state")
    if str(metadata(field, "typeOfLevel") or "").lower() != "surface":
        raise ValueError("SKT must be a surface field")

    specification = grid_spec(field)
    grib_uid = normalized_uuid(str(specification.get("uuidOfHGrid", "")))
    with netCDF4.Dataset(args.icon_extpar) as extpar:
        extpar_uid = normalized_uuid(str(extpar.getncattr("uuidOfHGrid")))
        if not grib_uid or grib_uid != extpar_uid:
            raise ValueError(f"EXTPAR grid UUID {extpar_uid} differs from GRIB {grib_uid}")
        clat_variable = extpar["clat"]
        clon_variable = extpar["clon"]
        source_lat = coordinates_in_degrees(
            _cell_variable(extpar, "clat"), getattr(clat_variable, "units", "radian")
        )
        source_lon = coordinates_in_degrees(
            _cell_variable(extpar, "clon"), getattr(clon_variable, "units", "radian")
        )
        source_land = _cell_variable(extpar, "FR_LAND") >= 0.5
    source_skt = np.asarray(field.to_numpy(flatten=True), dtype=np.float64)
    if source_skt.size != source_lat.size:
        raise ValueError("SKT cell count differs from the matching EXTPAR grid")

    diagnostics = build_target_sst_product(
        args.output,
        source_skt=source_skt,
        source_lat=source_lat,
        source_lon=source_lon,
        source_land=source_land,
        static_path=args.static,
        weights=RBFWeights.read(args.weights),
        valid_time=valid_time,
        source_path=args.surface_grib,
    )
    print(
        f"PASS: wrote {args.output} for {diagnostics['valid_time']} "
        f"water_cells={diagnostics['water_cell_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
