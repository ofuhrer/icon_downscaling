#!/usr/bin/env python3
"""Profile one real-sized hicarprep target record with phase and hash timings.

This is deliberately a measurement driver, not an alternative production
pipeline.  It calls the maintained transformation and writers directly and
replays the checksum work performed by the CLI so the recurring cost is
visible in a machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time

import netCDF4
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preprocessing.hicarprep import pipeline, sst
from preprocessing.hicarprep.pipeline import (
    convert_water_to_hicar_mixing_ratios,
    transform_icon_state,
    write_boundary_condition,
    write_hicar_forcing_record,
)
from preprocessing.hicarprep.products import sha256
from preprocessing.hicarprep.remap import RBFWeights, grid_fingerprint


def _rss_bytes() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


class Measurements:
    def __init__(self) -> None:
        self.phases: list[dict[str, object]] = []
        self.hashes: list[dict[str, object]] = []

    def phase(self, name: str, function, *args, **kwargs):
        started = time.perf_counter()
        user_started = resource.getrusage(resource.RUSAGE_SELF)
        result = function(*args, **kwargs)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self.phases.append(
            {
                "name": name,
                "wall_seconds": time.perf_counter() - started,
                "user_seconds": usage.ru_utime - user_started.ru_utime,
                "system_seconds": usage.ru_stime - user_started.ru_stime,
                "maximum_rss_bytes": _rss_bytes(),
            }
        )
        return result

    def timed_hash(self, caller: str, original):
        def measured(path: Path) -> str:
            path = Path(path)
            started = time.perf_counter()
            value = original(path)
            self.hashes.append(
                {
                    "caller": caller,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "wall_seconds": time.perf_counter() - started,
                    "sha256": value,
                }
            )
            return value

        return measured


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_sst_fixture(args: argparse.Namespace) -> int:
    """Recover the exact target SST payload from a retained forcing record."""
    static_digest = sha256(args.static)
    with netCDF4.Dataset(args.forcing) as forcing, netCDF4.Dataset(args.static) as static:
        valid_time = str(getattr(forcing, "valid_time"))
        lat = np.asarray(static["lat"][:], dtype=np.float64)
        lon = np.asarray(static["lon"][:], dtype=np.float64)
        land = np.asarray(static["landmask"][:], dtype=np.float64) >= 0.5
        if not np.array_equal(forcing["lat_1"][:], lat) or not np.array_equal(
            forcing["lon_1"][:], lon
        ):
            raise ValueError("retained forcing and benchmark static grids differ")
        values = {
            "SST": np.asarray(forcing["SST"][0], dtype=np.float32),
            "unsupported_water_mask": np.asarray(
                forcing["SST_unsupported_water_mask"][:], dtype=np.int8
            ),
            "nearest_same_surface_candidate_distance_km": np.asarray(
                forcing["SST_nearest_same_surface_candidate_distance_km"][:],
                dtype=np.float64,
            ),
        }
        attributes = {
            "product_type": "hicarprep_target_water_temperature",
            "hicarprep_product_version": str(
                getattr(forcing, "hicarprep_product_version")
            ),
            "valid_time": valid_time,
            "static_sha256": static_digest,
            "target_grid_fingerprint": grid_fingerprint(lat, lon),
            "source_variable": "SKT",
            "source_sha256": str(getattr(forcing, "sst_native_source_sha256")),
            "sst_policy_version": str(getattr(forcing, "sst_policy_version")),
            "remap_policy": str(getattr(forcing, "sst_remap_policy")),
            "water_cell_count": int(getattr(forcing, "sst_water_cell_count")),
            "water_compact_fallback_count": int(
                getattr(forcing, "sst_water_compact_fallback_count")
            ),
            "water_unsupported_count": int(
                getattr(forcing, "sst_water_unsupported_count")
            ),
            "maximum_nearest_same_surface_candidate_distance_km": float(
                getattr(forcing, "sst_maximum_nearest_same_surface_candidate_distance_km")
            ),
            "benchmark_fixture_source": str(args.forcing),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.partial")
    try:
        with netCDF4.Dataset(temporary, "w") as output:
            output.createDimension("y", lat.shape[0])
            output.createDimension("x", lat.shape[1])
            output.createVariable("lat", "f8", ("y", "x"), zlib=True)[:] = lat
            output.createVariable("lon", "f8", ("y", "x"), zlib=True)[:] = lon
            output.createVariable("water_mask", "i1", ("y", "x"), zlib=True)[:] = ~land
            sst_variable = output.createVariable("SST", "f4", ("y", "x"), zlib=True)
            sst_variable[:] = values["SST"]
            sst_variable.units = "K"
            output.createVariable(
                "unsupported_water_mask", "i1", ("y", "x"), zlib=True
            )[:] = values["unsupported_water_mask"]
            output.createVariable(
                "nearest_same_surface_candidate_distance_km",
                "f8",
                ("y", "x"),
                zlib=True,
                fill_value=np.nan,
            )[:] = values["nearest_same_surface_candidate_distance_km"]
            output.setncatts(attributes)
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(args.output)
    return 0


def profile_record(args: argparse.Namespace) -> int:
    measurements = Measurements()
    original_pipeline_hash = pipeline.sha256
    original_sst_hash = sst.sha256
    pipeline.sha256 = measurements.timed_hash("pipeline", original_pipeline_hash)
    sst.sha256 = measurements.timed_hash("sst", original_sst_hash)
    total_started = time.perf_counter()
    try:
        weights = measurements.phase("read_weights", RBFWeights.read, args.weights)
        state, diagnostics = measurements.phase(
            "transform_icon_state",
            transform_icon_state,
            args.icon_state,
            args.static,
            weights,
            column_workers=args.column_workers,
            rbf_backend=args.rbf_backend,
        )
        state = measurements.phase(
            "convert_water_to_hicar_mixing_ratios",
            convert_water_to_hicar_mixing_ratios,
            state,
        )
        measurements.phase(
            "write_regular_forcing",
            write_hicar_forcing_record,
            args.output,
            state,
            diagnostics,
            static_path=args.static,
            source_path=args.icon_state,
            target_sst_path=args.target_sst,
            lateral_relaxation_authority="hicarprep sparse_lbc_file_list",
        )
        with netCDF4.Dataset(args.static) as static:
            x = np.asarray(static["x"][:], dtype=np.float64)
            y = np.asarray(static["y"][:], dtype=np.float64)
        measurements.phase(
            "write_sparse_boundary",
            write_boundary_condition,
            args.boundary,
            state,
            x=x,
            y=y,
            boundary_width_m=args.boundary_width_m,
            initial_condition_path=args.output,
            valid_time=str(diagnostics["valid_time"]),
            water_representation="dry-air mixing ratio",
        )
        # Replay the current CLI's unconditional manifest checksum work.
        for name, path in (
            ("source", args.icon_state),
            ("static", args.static),
            ("target_sst", args.target_sst),
            ("weights", args.weights),
            ("output", args.output),
            ("forcing", args.output),
            ("boundary", args.boundary),
        ):
            measurements.timed_hash(f"cli_manifest:{name}", sha256)(path)
    finally:
        pipeline.sha256 = original_pipeline_hash
        sst.sha256 = original_sst_hash
    payload = {
        "schema": "hicarprep-swiss-input-performance-v1",
        "source_commit": args.source_commit,
        "host": os.uname().nodename,
        "input": {
            "icon_state": str(args.icon_state),
            "static": str(args.static),
            "weights": str(args.weights),
            "target_sst": str(args.target_sst),
            "target_shape": list(state["lat"].shape),
            "vertical_levels": int(state["T"].shape[0]),
            "column_workers": args.column_workers,
            "rbf_backend": args.rbf_backend,
            "boundary_width_m": args.boundary_width_m,
        },
        "output": {
            "forcing": str(args.output),
            "forcing_size_bytes": args.output.stat().st_size,
            "boundary": str(args.boundary),
            "boundary_size_bytes": args.boundary.stat().st_size,
        },
        "diagnostics": diagnostics,
        "phases": measurements.phases,
        "hashes": measurements.hashes,
        "total_wall_seconds": time.perf_counter() - total_started,
        "maximum_rss_bytes": _rss_bytes(),
    }
    _atomic_json(args.report, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    fixture = commands.add_parser("build-sst-fixture")
    fixture.add_argument("--forcing", type=Path, required=True)
    fixture.add_argument("--static", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    fixture.set_defaults(function=build_sst_fixture)

    profile = commands.add_parser("profile-record")
    profile.add_argument("--icon-state", type=Path, required=True)
    profile.add_argument("--static", type=Path, required=True)
    profile.add_argument("--weights", type=Path, required=True)
    profile.add_argument("--target-sst", type=Path, required=True)
    profile.add_argument("--output", type=Path, required=True)
    profile.add_argument("--boundary", type=Path, required=True)
    profile.add_argument("--report", type=Path, required=True)
    profile.add_argument("--source-commit", required=True)
    profile.add_argument("--column-workers", type=int, default=8)
    profile.add_argument("--rbf-backend", choices=("numpy", "numba"), default="numpy")
    profile.add_argument("--boundary-width-m", type=float, default=10_000.0)
    profile.set_defaults(function=profile_record)
    return result


def main() -> int:
    args = parser().parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
