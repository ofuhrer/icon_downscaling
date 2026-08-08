#!/usr/bin/env python3
"""Render the selected Swiss 200 m R&D configuration."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import re

import netCDF4
import numpy as np


CASE = Path(__file__).resolve().parents[1]
TEMPLATE = CASE / "config" / "hicar_swiss_200m.nml.in"
TOKEN = re.compile(r"@[A-Z_]+@")
SELECTED_NZ = 80
SELECTED_MODEL_TOP_M = 12_000.0
SELECTED_LOWEST_LAYER_M = 26.0
SELECTED_STRETCH_FACTOR = 0.65
SELECTED_MINIMUM_LAYER_THICKNESS_M = 20.0


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "").replace("T", " "))


def listed_paths(path: Path) -> list[Path]:
    if not path.is_file():
        raise SystemExit(f"missing file list: {path}")
    return [Path(line.strip().strip('"')) for line in path.read_text().splitlines() if line.strip()]


def forcing_time(path: Path) -> datetime:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise SystemExit(f"forcing record is incomplete: {path}")
    with netCDF4.Dataset(path) as dataset:
        if getattr(dataset, "product_type", "") != "hicarprep_target_forcing_record":
            raise SystemExit(f"not a hicarprep target forcing record: {path}")
        if getattr(dataset, "water_representation", "") != "dry-air mixing ratio":
            raise SystemExit(f"forcing record does not use dry-air mixing ratios: {path}")
        variable = dataset["time"]
        value = netCDF4.num2date(
            variable[0], variable.units, calendar=getattr(variable, "calendar", "standard")
        )
    return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)


def boundary_time(path: Path) -> datetime:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise SystemExit(f"sparse LBC frame is incomplete: {path}")
    with netCDF4.Dataset(path) as dataset:
        if getattr(dataset, "product_type", "") != "hicar_lateral_boundary_state":
            raise SystemExit(f"not a hicarprep sparse LBC frame: {path}")
        return timestamp(str(dataset.valid_time))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument("--forcing-file-list", type=Path, required=True)
    parser.add_argument("--sparse-lbc-file-list", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restart-dir", type=Path, required=True)
    parser.add_argument("--restart-from")
    parser.add_argument("--restart-interval", type=int, default=24)
    parser.add_argument("--output-interval", type=int, default=3600)
    parser.add_argument(
        "--output-profile", choices=("station", "evaluation", "debug"), default="evaluation"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start, end = timestamp(args.start_date), timestamp(args.end_date)
    if end <= start:
        raise SystemExit("--end-date must be after --start-date")
    if args.output_interval <= 0 or args.restart_interval <= 0:
        raise SystemExit("output and restart intervals must be positive")
    if not args.static_file.is_file():
        raise SystemExit(f"missing runtime domain: {args.static_file}")
    with netCDF4.Dataset(args.static_file) as static:
        missing = sorted(
            {
                "lat", "lon", "topo", "HHL", "HFL", "landmask", "landuse",
                "soil_type_layer", "swe", "snow_height",
            }
            - set(static.variables)
        )
        if missing:
            raise SystemExit("runtime domain lacks selected land fields: " + ", ".join(missing))
        soil = static["soil_type_layer"]
        if soil.ndim != 3 or soil.shape[0] != 4:
            raise SystemExit("soil_type_layer must contain four Noah-MP layers")
        horizontal = static["lat"].shape
        if static["HHL"].shape != (81, *horizontal) or static["HFL"].shape != (80, *horizontal):
            raise SystemExit("runtime domain HHL/HFL do not match the selected 80-level grid")
        geometry_settings = {
            "sleve_nz": SELECTED_NZ,
            "sleve_model_top_m": SELECTED_MODEL_TOP_M,
            "sleve_lowest_layer_m": SELECTED_LOWEST_LAYER_M,
            "sleve_stretch_factor": SELECTED_STRETCH_FACTOR,
            "required_minimum_sleve_layer_thickness_m": (
                SELECTED_MINIMUM_LAYER_THICKNESS_M
            ),
        }
        mismatches = {
            name: {"actual": getattr(static, name, None), "expected": expected}
            for name, expected in geometry_settings.items()
            if getattr(static, name, None) != expected
        }
        if mismatches:
            raise SystemExit(f"runtime domain SLEVE settings do not match the namelist: {mismatches}")
        hhl = np.asarray(static["HHL"][:], dtype=np.float64)
        thickness = np.diff(hhl, axis=0)
        if (
            not np.isfinite(hhl).all()
            or np.any(thickness <= SELECTED_MINIMUM_LAYER_THICKNESS_M)
            or not np.allclose(hhl[0], static["topo"][:], atol=1.0e-8, rtol=0.0)
            or not np.allclose(hhl[-1], SELECTED_MODEL_TOP_M, atol=1.0e-8, rtol=0.0)
        ):
            raise SystemExit("runtime domain violates the selected SLEVE geometry bounds")

    forcing = listed_paths(args.forcing_file_list)
    boundaries = listed_paths(args.sparse_lbc_file_list)
    forcing_times = [forcing_time(path) for path in forcing]
    boundary_times = [boundary_time(path) for path in boundaries]
    if len(forcing_times) < 2 or forcing_times != boundary_times:
        raise SystemExit("forcing and sparse LBC lists must contain the same two or more times")
    if forcing_times[0] != start or forcing_times[-1] < end:
        raise SystemExit("input sequence must bracket the complete simulation segment")
    if any(right - left != timedelta(hours=1) for left, right in zip(forcing_times, forcing_times[1:])):
        raise SystemExit("input sequence must be continuous at one-hour cadence")

    restart_lines = f"  restartinterval = {args.restart_interval}"
    if args.restart_from:
        if timestamp(args.restart_from) != start:
            raise SystemExit("--restart-from must equal --start-date")
        restart_lines += (
            "\n  restart_run = .True."
            f"\n  restart_date = '{args.restart_from}'"
            "\n  override_check = .False."
        )

    output_variables = {
        "station": "'precipitation', 'psfc', 'taix', 'hus2m', 'u10m', 'v10m'",
        "evaluation": (
            "'precipitation', 'snowfall', 'psfc', 'taix', 'hus2m', 'u10m', 'v10m', "
            "'u_agl', 'v_agl', 'rho_agl', 'ustar', 'surface_roughness', 'sfc_Ri', 'hpbl', "
            "'rsds', 'lwtr', 'rlus', 'hfgs', 'hfss', 'hfls', 'tsfe', 'albedo', "
            "'snow_height', 'soil_column_total_water', 'soil_water_content', 'soil_temperature'"
        ),
        "debug": (
            "'u', 'v', 'w', 'pressure', 'temperature', 'potential_temperature', "
            "'qv', 'qc', 'qi', 'qr', 'qs', 'qg', 'density', 'z', 'z_i', "
            "'precipitation', 'psfc', 'taix', 'u10m', 'v10m'"
        ),
    }
    values = {
        "@START_DATE@": args.start_date,
        "@END_DATE@": args.end_date,
        "@NZ@": str(SELECTED_NZ),
        "@LOWEST_LAYER_M@": str(SELECTED_LOWEST_LAYER_M),
        "@ADVECT_DENSITY@": ".True.",
        "@STATIC_FILE@": str(args.static_file.resolve()),
        "@FORCING_FILE_LIST@": str(args.forcing_file_list.resolve()),
        "@SPARSE_LBC_FILE_LIST@": str(args.sparse_lbc_file_list.resolve()),
        "@OUTPUT_DIR@": f"{args.output_dir.resolve()}/",
        "@RESTART_DIR@": f"{args.restart_dir.resolve()}/",
        "@RESTART_LINES@": restart_lines,
        "@OUTPUT_INTERVAL@": str(args.output_interval),
        "@OUTPUT_VARS@": output_variables[args.output_profile],
    }
    rendered = TEMPLATE.read_text()
    for token, value in values.items():
        rendered = rendered.replace(token, value)
    unresolved = TOKEN.findall(rendered)
    if unresolved:
        raise SystemExit("unresolved namelist tokens: " + ", ".join(unresolved))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.restart_dir.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
