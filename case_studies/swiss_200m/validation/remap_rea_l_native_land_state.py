#!/usr/bin/env python3
"""Remap native-grid REA-L land state directly to a HICAR static grid."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import sys
import time
from typing import Callable

import netCDF4
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preprocessing.hicarprep.surface import (  # noqa: E402
    ICON_TERRA_FIELD_CAPACITY,
    ICON_TERRA_WILTING_POINT,
    ICON_T_SO_DEPTHS_M as REA_L_T_SO_DEPTHS_M,
    ICON_W_SO_BOUNDS_M as REA_L_W_SO_BOUNDS_M,
    icon_soil_water_to_smi,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_uuid(value: str) -> str:
    return value.lower().replace("-", "")


def chunk_slices(size: int, chunk_size: int) -> list[slice]:
    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    return [slice(start, min(size, start + chunk_size)) for start in range(0, size, chunk_size)]


def progress(event: str, **details) -> None:
    """Emit one bounded, crash-surviving progress record."""
    print(json.dumps({"event": event, **details}, sort_keys=True), flush=True)


def configure_runtime_caches(root: Path) -> Path:
    """Keep MIR weights and eckit target-grid geometry in project scratch."""
    root = root.resolve()
    geometry = root / "eckit_geo"
    root.mkdir(parents=True, exist_ok=True)
    geometry.mkdir(parents=True, exist_ok=True)
    os.environ["MIR_CACHE_PATH"] = str(root)
    os.environ["ECKIT_GEO_CACHE_PATH"] = str(geometry)
    return geometry


def build_target_chunks(
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
    row_chunk: int,
    grid_factory: Callable,
) -> list[tuple[slice, object, str]]:
    """Register target chunks once and retain their compact eckit grids."""
    target_latitude = np.asarray(target_latitude, dtype=np.float64)
    target_longitude = np.asarray(target_longitude, dtype=np.float64)
    if target_latitude.shape != target_longitude.shape or target_latitude.ndim != 2:
        raise ValueError("target latitude/longitude must be matching 2-D arrays")
    row_slices = chunk_slices(target_latitude.shape[0], row_chunk)
    chunks = []
    for chunk_index, row_slice in enumerate(row_slices):
        point_count = target_latitude[row_slice].size
        progress(
            "target_chunk_start",
            chunk_index=chunk_index,
            chunk_count=len(row_slices),
            row_start=row_slice.start,
            row_stop=row_slice.stop,
            point_count=point_count,
        )
        # Passing thousands of coordinate literals directly to MIR triggers
        # catastrophic std::regex recursion in eckit-geo. Constructing a Grid
        # first registers the coordinates and gives MIR the compact UID-backed
        # specification instead.
        out_grid = grid_factory(
            {
                "type": "unstructured",
                "latitudes": target_latitude[row_slice].ravel().tolist(),
                "longitudes": target_longitude[row_slice].ravel().tolist(),
            }
        )
        out_spec = dict(out_grid.spec)
        out_uid = str(out_spec.get("uid", ""))
        if out_spec.get("type") != "unstructured_ll" or not out_uid:
            raise ValueError(f"target chunk lacks a compact unstructured grid spec: {out_spec}")
        progress(
            "target_chunk_ready",
            chunk_index=chunk_index,
            row_start=row_slice.start,
            row_stop=row_slice.stop,
            point_count=point_count,
            target_uid=out_uid,
        )
        chunks.append((row_slice, out_grid, out_uid))
    return chunks


def chunked_regrid(
    values: np.ndarray,
    in_grid: dict,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
    interpolation: str,
    regrid: Callable,
    target_chunks: list[tuple[slice, object, str]],
    label: str = "field",
) -> np.ndarray:
    """Regrid fields in target-row chunks so MIR matrices remain bounded."""
    # MIR ArrayInput accepts only floating-point arrays. Categorical fields
    # remain exact because they use nearest-neighbour interpolation and are
    # rounded back to integer types when written.
    values = np.asarray(values, dtype=np.float64)
    target_latitude = np.asarray(target_latitude, dtype=np.float64)
    target_longitude = np.asarray(target_longitude, dtype=np.float64)
    if target_latitude.shape != target_longitude.shape or target_latitude.ndim != 2:
        raise ValueError("target latitude/longitude must be matching 2-D arrays")
    if values.ndim == 1:
        values = values[np.newaxis, :]
        squeeze = True
    elif values.ndim == 2:
        squeeze = False
    else:
        raise ValueError("native values must have shape (cell,) or (field, cell)")

    result = np.empty((values.shape[0],) + target_latitude.shape, dtype=np.float64)
    for chunk_index, (row_slice, out_grid, out_uid) in enumerate(target_chunks):
        point_count = target_latitude[row_slice].size
        for field_index in range(values.shape[0]):
            progress(
                "mir_regrid_start",
                label=label,
                interpolation=interpolation,
                chunk_index=chunk_index,
                row_start=row_slice.start,
                row_stop=row_slice.stop,
                point_count=point_count,
                field_index=field_index,
                field_count=values.shape[0],
                target_uid=out_uid,
            )
            started = time.monotonic()
            remapped = regrid(
                values[field_index],
                in_grid=in_grid,
                out_grid=out_grid,
                interpolation=interpolation,
                backend="mir",
            )
            if isinstance(remapped, tuple):
                remapped = remapped[0]
            remapped = np.asarray(remapped)
            if remapped.size != point_count:
                raise ValueError(
                    f"MIR returned {remapped.size} values for {point_count} target points"
                )
            result[field_index, row_slice] = remapped.reshape(target_latitude[row_slice].shape)
            progress(
                "mir_regrid_complete",
                label=label,
                interpolation=interpolation,
                chunk_index=chunk_index,
                row_start=row_slice.start,
                row_stop=row_slice.stop,
                point_count=point_count,
                field_index=field_index,
                field_count=values.shape[0],
                target_uid=out_uid,
                elapsed_seconds=round(time.monotonic() - started, 6),
            )
    return result[0] if squeeze else result


def normalized_supported_values(
    weighted_values: np.ndarray,
    support: np.ndarray,
    minimum_support: float = 1.0e-12,
) -> np.ndarray:
    """Normalize an interpolated field over only its finite source support."""
    weighted_values = np.asarray(weighted_values, dtype=np.float64)
    support = np.asarray(support, dtype=np.float64)
    if weighted_values.shape != support.shape:
        raise ValueError("weighted values and support must have the same shape")
    result = np.full(weighted_values.shape, np.nan, dtype=np.float64)
    np.divide(
        weighted_values,
        support,
        out=result,
        where=np.isfinite(support) & (support > minimum_support),
    )
    return result


def chunked_regrid_with_normalized_support(
    values: np.ndarray,
    in_grid: dict,
    target_latitude: np.ndarray,
    target_longitude: np.ndarray,
    interpolation: str,
    regrid: Callable,
    target_chunks: list[tuple[slice, object, str]],
    label: str,
    source_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Regrid a continuous field without diluting it across missing support."""
    values = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(values)
    if source_mask is not None:
        source_mask = np.asarray(source_mask, dtype=bool)
        try:
            source_mask = np.broadcast_to(source_mask, values.shape)
        except ValueError as error:
            raise ValueError("source mask is not broadcastable to values") from error
        valid &= source_mask
    weighted = chunked_regrid(
        np.where(valid, values, 0.0),
        in_grid,
        target_latitude,
        target_longitude,
        interpolation,
        regrid,
        target_chunks,
        f"{label}_WEIGHTED",
    )
    support = chunked_regrid(
        valid.astype(np.float64),
        in_grid,
        target_latitude,
        target_longitude,
        interpolation,
        regrid,
        target_chunks,
        f"{label}_SUPPORT",
    )
    return normalized_supported_values(weighted, support), support


def earthkit_runtime():
    # fdb/5.21:v1 currently aborts from duplicate eckit factories unless MIR
    # is imported before EarthKit. Keep this ordering explicit and tested on
    # Balfrin until APNRZ-998 is resolved.
    import mir  # noqa: F401
    import earthkit.data as ekd
    from eckit.geo import Grid
    from earthkit.geo.grids.array import regrid

    return ekd, regrid, Grid


def metadata(field, key: str):
    try:
        return field.metadata(key)
    except Exception:
        return None


def fixed_surface_depth(field) -> float:
    """Decode the physical first/second fixed-surface value from GRIB."""
    candidates: list[float] = []
    for suffix in ("FirstFixedSurface", "SecondFixedSurface"):
        scaled = metadata(field, f"scaledValueOf{suffix}")
        factor = metadata(field, f"scaleFactorOf{suffix}")
        if scaled is None or factor is None or int(scaled) == 2**32 - 1:
            continue
        candidates.append(float(scaled) * 10.0 ** (-int(factor)))
    if not candidates:
        level = metadata(field, "level")
        if level is None:
            raise ValueError("soil field has no decodable fixed-surface depth")
        return float(level)
    return max(candidates)


def field_array(field) -> np.ndarray:
    return np.asarray(field.to_numpy(flatten=True), dtype=np.float64)


def grid_spec(field) -> dict:
    return dict(field.geography.grid_spec())


def read_grib_fields(path: Path):
    ekd, _, _ = earthkit_runtime()
    return list(ekd.from_source("file", str(path)).to_fieldlist())


def soil_stack(fields: list, expected_depths: np.ndarray, name: str) -> tuple[np.ndarray, dict]:
    if len(fields) != expected_depths.size:
        raise ValueError(f"{name} has {len(fields)} fields instead of {expected_depths.size}")
    ordered = sorted(fields, key=fixed_surface_depth)
    decoded = np.array([fixed_surface_depth(field) for field in ordered])
    if not np.allclose(decoded, expected_depths, atol=5.0e-7):
        raise ValueError(
            f"{name} depths {decoded.tolist()} disagree with expected {expected_depths.tolist()}"
        )
    reference_grid = grid_spec(ordered[0])
    values = np.stack([field_array(field) for field in ordered])
    for field in ordered[1:]:
        if grid_spec(field) != reference_grid:
            raise ValueError(f"{name} fields do not share one native grid")
    return values, reference_grid


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-static", required=True, type=Path)
    parser.add_argument("--icon-extpar", required=True, type=Path)
    parser.add_argument("--surface-grib", required=True, type=Path)
    parser.add_argument("--soil-temperature-grib", required=True, type=Path)
    parser.add_argument("--soil-water-grib", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--mir-cache", required=True, type=Path)
    parser.add_argument("--row-chunk", type=int, default=32)
    args = parser.parse_args()

    inputs = (
        args.target_static,
        args.icon_extpar,
        args.surface_grib,
        args.soil_temperature_grib,
        args.soil_water_grib,
    )
    for path in inputs:
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"missing or empty input: {path}")
    if args.output.exists() or Path(f"{args.output}.ready").exists():
        raise SystemExit(f"refusing to overwrite published output: {args.output}")

    eckit_geo_cache = configure_runtime_caches(args.mir_cache)
    _, regrid, grid_factory = earthkit_runtime()

    surface_fields = read_grib_fields(args.surface_grib)
    surface_by_name = {metadata(field, "shortName"): field for field in surface_fields}
    required_surface = ("SKT", "W_SNOW", "RHO_SNOW")
    missing = [name for name in required_surface if name not in surface_by_name]
    if missing:
        raise ValueError(f"surface GRIB lacks {missing}")

    t_so, t_so_grid = soil_stack(
        read_grib_fields(args.soil_temperature_grib),
        REA_L_T_SO_DEPTHS_M,
        "T_SO",
    )
    w_so, w_so_grid = soil_stack(
        read_grib_fields(args.soil_water_grib),
        REA_L_W_SO_BOUNDS_M[1:],
        "W_SO",
    )
    surface_grid = grid_spec(surface_by_name["SKT"])
    if surface_grid != t_so_grid or surface_grid != w_so_grid:
        raise ValueError("surface, T_SO and W_SO do not share one ICON native grid")
    source_uid = normalized_uuid(str(surface_grid.get("uid", surface_grid.get("uuid", ""))))

    with netCDF4.Dataset(args.icon_extpar) as extpar:
        extpar_uid = normalized_uuid(str(extpar.getncattr("uuidOfHGrid")))
        if source_uid and source_uid != extpar_uid:
            raise ValueError(f"EXTPAR grid UUID {extpar_uid} differs from GRIB {source_uid}")
        soil_type_native = np.asarray(extpar.variables["SOILTYP"][:])
        if soil_type_native.size != w_so.shape[1]:
            raise ValueError("EXTPAR cell count differs from REA-L native GRIB")
        smi_native = icon_soil_water_to_smi(w_so, soil_type_native)
        extpar_fields = {
            "SOILTYP": soil_type_native,
            "FR_LAND": np.asarray(extpar.variables["FR_LAND"][:]),
            "HSURF": np.asarray(extpar.variables["topography_c"][:]),
            "ROOTDP": np.asarray(extpar.variables["ROOTDP"][:]),
            "ICON_LU_CLASS": np.argmax(
                np.asarray(extpar.variables["LU_CLASS_FRACTION"][:]), axis=0
            ).astype(np.int16)
            + 1,
        }

    with netCDF4.Dataset(args.target_static) as target:
        target_latitude = np.asarray(target.variables["lat"][:])
        target_longitude = np.asarray(target.variables["lon"][:])
    target_shape = target_latitude.shape
    target_chunks = build_target_chunks(
        target_latitude, target_longitude, args.row_chunk, grid_factory
    )

    continuous = {
        "SKT": field_array(surface_by_name["SKT"]),
        "W_SNOW": field_array(surface_by_name["W_SNOW"]),
        "RHO_SNOW": field_array(surface_by_name["RHO_SNOW"]),
        "T_SO": t_so,
        "FR_LAND": extpar_fields["FR_LAND"],
        "HSURF": extpar_fields["HSURF"],
        "ROOTDP": extpar_fields["ROOTDP"],
    }
    categorical = {
        "SOILTYP": extpar_fields["SOILTYP"],
        "ICON_LU_CLASS": extpar_fields["ICON_LU_CLASS"],
    }
    remapped = {
        name: chunked_regrid(
            values,
            surface_grid,
            target_latitude,
            target_longitude,
            "linear",
            regrid,
            target_chunks,
            name,
        )
        for name, values in continuous.items()
    }
    remapped["W_SO"], w_so_support = chunked_regrid_with_normalized_support(
        w_so,
        surface_grid,
        target_latitude,
        target_longitude,
        "linear",
        regrid,
        target_chunks,
        "W_SO",
        source_mask=(soil_type_native >= 1) & (soil_type_native <= 8),
    )
    remapped["SMI"], smi_support = chunked_regrid_with_normalized_support(
        smi_native,
        surface_grid,
        target_latitude,
        target_longitude,
        "linear",
        regrid,
        target_chunks,
        "SMI",
    )
    remapped.update(
        {
            name: chunked_regrid(
                values,
                surface_grid,
                target_latitude,
                target_longitude,
                "nearest-neighbour",
                regrid,
                target_chunks,
                name,
            )
            for name, values in categorical.items()
        }
    )
    remapped["FR_LAND"] = np.clip(remapped["FR_LAND"], 0.0, 1.0)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.name}.partial.{os.getpid()}")
    try:
        with netCDF4.Dataset(partial, "w") as output:
            ny, nx = target_shape
            output.createDimension("y", ny)
            output.createDimension("x", nx)
            output.createDimension("t_so_depth", REA_L_T_SO_DEPTHS_M.size)
            output.createDimension("w_so_layer", REA_L_W_SO_BOUNDS_M.size - 1)
            for name, values, dims in (
                ("lat", target_latitude, ("y", "x")),
                ("lon", target_longitude, ("y", "x")),
                ("SKT", remapped["SKT"], ("y", "x")),
                ("W_SNOW", remapped["W_SNOW"], ("y", "x")),
                ("RHO_SNOW", remapped["RHO_SNOW"], ("y", "x")),
                ("T_SO", remapped["T_SO"], ("t_so_depth", "y", "x")),
                ("W_SO", remapped["W_SO"], ("w_so_layer", "y", "x")),
                ("W_SO_SOURCE_SUPPORT", w_so_support, ("w_so_layer", "y", "x")),
                ("SMI", remapped["SMI"], ("w_so_layer", "y", "x")),
                ("SMI_SOURCE_SUPPORT", smi_support, ("w_so_layer", "y", "x")),
                ("FR_LAND", remapped["FR_LAND"], ("y", "x")),
                ("HSURF", remapped["HSURF"], ("y", "x")),
                ("ROOTDP", remapped["ROOTDP"], ("y", "x")),
            ):
                output.createVariable(name, "f4", dims, zlib=True)[:] = values
            for name in ("SOILTYP", "ICON_LU_CLASS"):
                output.createVariable(name, "i2", ("y", "x"), zlib=True)[:] = np.rint(
                    remapped[name]
                ).astype(np.int16)
            output.createVariable("t_so_depth", "f8", ("t_so_depth",))[:] = REA_L_T_SO_DEPTHS_M
            output.createVariable("w_so_layer", "f8", ("w_so_layer",))[:] = REA_L_W_SO_BOUNDS_M[1:]
            output.setncattr("source_grid_uuid", extpar_uid)
            output.setncattr(
                "spatial_regridding", "EarthKit 1.0 / MIR direct native ICON to HICAR points"
            )
            output.setncattr("mir_cache_path", str(args.mir_cache.resolve()))
            output.setncattr("eckit_geo_cache_path", str(eckit_geo_cache))
            output.setncattr("row_chunk", args.row_chunk)
        os.replace(partial, args.output)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise

    payload = {
        "schema_version": 1,
        "status": "PASS",
        "source_grid_uuid": extpar_uid,
        "target_shape": list(target_shape),
        "row_chunk": args.row_chunk,
        "interpolation": {
            "continuous": "linear",
            "categorical": "nearest-neighbour",
            "soil_water_absolute": "linear native ICON layer-integrated W_SO",
            "soil_water_smi": "ICON W_SO -> TERRA SMI on native grid, then linear",
            "soil_water_missing": "linear(value * finite land support) / linear(finite land support)",
        },
        "icon_terra_field_capacity": ICON_TERRA_FIELD_CAPACITY.tolist(),
        "icon_terra_wilting_point": ICON_TERRA_WILTING_POINT.tolist(),
        "inputs": {str(path): sha256(path) for path in inputs},
        "output": str(args.output.resolve()),
        "output_sha256": sha256(args.output),
        "mir_cache": str(args.mir_cache.resolve()),
        "eckit_geo_cache": str(eckit_geo_cache),
    }
    write_json_atomic(args.manifest, payload)
    Path(f"{args.manifest}.ready").touch()
    Path(f"{args.output}.ready").touch()
    print(f"PASS: published {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
