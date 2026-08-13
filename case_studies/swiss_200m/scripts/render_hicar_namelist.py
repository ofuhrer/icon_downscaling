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
SELECTED_MODEL_TOP_M = 15_000.0
SELECTED_LOWEST_LAYER_M = 20.0
SELECTED_STRETCH_FACTOR = 0.65
SELECTED_MINIMUM_LAYER_THICKNESS_M = 12.0


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
    parser.add_argument(
        "--sparse-lbc-file-list",
        type=Path,
        help="optional sparse target-grid boundary list; omit for regular-forcing relaxation",
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--restart-dir", type=Path, required=True)
    parser.add_argument("--restart-from")
    parser.add_argument(
        "--restart-override-check",
        action="store_true",
        help=(
            "permit an intentional configuration change from --restart-from; "
            "the default remains fail-closed"
        ),
    )
    parser.add_argument("--restart-interval", type=int, default=24)
    parser.add_argument("--output-interval", type=int, default=600)
    parser.add_argument("--radiation-update-interval", type=float, default=600.0)
    parser.add_argument(
        "--radiation-scheme", choices=("rrtmgp", "rrtmg"), default="rrtmgp"
    )
    parser.add_argument(
        "--output-profile", choices=("station", "evaluation", "debug"), default="evaluation"
    )
    parser.add_argument("--model-debug", action="store_true")
    parser.add_argument("--disable-sx", action="store_true")
    parser.add_argument(
        "--alpha-const",
        type=float,
        default=1.0,
        help="fixed wind-solver alpha in 0.01..1, or -1 for dynamic Froude alpha",
    )
    parser.add_argument("--cfl-reduction-factor", type=float, default=1.6)
    parser.add_argument(
        "--require-land-climatology",
        action="store_true",
        help="reject runtime domains without both VEGFRA and LAI",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start, end = timestamp(args.start_date), timestamp(args.end_date)
    if end <= start:
        raise SystemExit("--end-date must be after --start-date")
    if (
        args.output_interval <= 0
        or args.restart_interval <= 0
        or args.radiation_update_interval <= 0.0
    ):
        raise SystemExit("output and restart intervals must be positive")
    if not 0.0 < args.cfl_reduction_factor <= 1.6:
        raise SystemExit("--cfl-reduction-factor must be in (0, 1.6]")
    if args.alpha_const != -1.0 and not 0.01 <= args.alpha_const <= 1.0:
        raise SystemExit("--alpha-const must be -1 (dynamic) or in [0.01, 1]")
    if not args.static_file.is_file():
        raise SystemExit(f"missing runtime domain: {args.static_file}")
    land_climatology_lines: list[str] = []
    monthly_vegfrac = False
    snow_temperature_line = ""
    with netCDF4.Dataset(args.static_file) as static:
        missing = sorted(
            {
                "lat", "lon", "topo", "HHL", "HFL", "landmask", "landuse",
                "soil_type_layer", "swe", "snow_height", "hlm", "svf",
                "slope_rad", "aspect_rad",
            }
            - set(static.variables)
        )
        if missing:
            raise SystemExit("runtime domain lacks selected land fields: " + ", ".join(missing))
        soil = static["soil_type_layer"]
        if soil.ndim != 3 or soil.shape[0] != 4:
            raise SystemExit("soil_type_layer must contain four Noah-MP layers")
        horizontal = static["lat"].shape
        if static["hlm"].shape != (90, *horizontal):
            raise SystemExit("hlm must contain 90 azimuths on the target y,x grid")
        for name in ("svf", "slope_rad", "aspect_rad"):
            values = np.asarray(static[name][:], dtype=np.float64)
            if values.shape != horizontal or not np.isfinite(values).all():
                raise SystemExit(f"{name} must be finite on the target y,x grid")
        if "snow_temperature_initial" in static.variables:
            snow_temperature = np.asarray(
                static["snow_temperature_initial"][:], dtype=np.float64
            )
            if snow_temperature.shape != horizontal or not np.isfinite(
                snow_temperature
            ).all():
                raise SystemExit("snow_temperature_initial must be finite on the target grid")
            snow_temperature_line = "  snow_temp_var = 'snow_temperature_initial'"

        available_climatology = set(static.variables) & {
            "VEGFRA", "LAI", "ALBEDO", "vegetation_fraction_max"
        }
        if args.require_land_climatology and not {"VEGFRA", "LAI"}.issubset(
            available_climatology
        ):
            missing = sorted({"VEGFRA", "LAI"} - available_climatology)
            raise SystemExit(
                "runtime domain lacks required land climatology fields: "
                + ", ".join(missing)
            )
        if "VEGFRA" in available_climatology:
            variable = static["VEGFRA"]
            valid_shapes = {horizontal, (12, *horizontal)}
            if variable.shape not in valid_shapes:
                raise SystemExit("VEGFRA must have y,x or 12-month month,y,x dimensions")
            values = np.asarray(variable[:], dtype=np.float64)
            if not np.isfinite(values).all() or np.any((values < 0.0) | (values > 100.0)):
                raise SystemExit("VEGFRA must be finite and in 0..100 percent")
            monthly_vegfrac = variable.shape == (12, *horizontal)
            land_climatology_lines.append("  vegfrac_var = 'VEGFRA'")
        for name, namelist_name, lower, upper in (
            ("LAI", "lai_var", 0.0, 20.0),
            ("ALBEDO", "albedo_var", 0.0, 1.0),
            ("vegetation_fraction_max", "vegfracmax_var", 0.0, 100.0),
        ):
            if name not in available_climatology:
                continue
            values = np.asarray(static[name][:], dtype=np.float64)
            if values.shape != horizontal or not np.isfinite(values).all() or np.any(
                (values < lower) | (values > upper)
            ):
                raise SystemExit(
                    f"{name} must be finite on y,x and lie in {lower:g}..{upper:g}"
                )
            land_climatology_lines.append(f"  {namelist_name} = '{name}'")
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
            or np.any(thickness < SELECTED_MINIMUM_LAYER_THICKNESS_M)
            or not np.allclose(hhl[0], static["topo"][:], atol=1.0e-8, rtol=0.0)
            or not np.allclose(hhl[-1], SELECTED_MODEL_TOP_M, atol=1.0e-8, rtol=0.0)
        ):
            raise SystemExit("runtime domain violates the selected SLEVE geometry bounds")

    forcing = listed_paths(args.forcing_file_list)
    forcing_times = [forcing_time(path) for path in forcing]
    if len(forcing_times) < 2:
        raise SystemExit("forcing list must contain two or more times")
    sparse_lbc_line = ""
    if args.sparse_lbc_file_list is not None:
        boundaries = listed_paths(args.sparse_lbc_file_list)
        boundary_times = [boundary_time(path) for path in boundaries]
        if forcing_times != boundary_times:
            raise SystemExit(
                "forcing and sparse LBC lists must contain the same two or more times"
            )
        sparse_lbc_line = (
            "  sparse_lbc_file_list = "
            f"'{args.sparse_lbc_file_list.resolve()}'"
        )
    if forcing_times[0] > start or forcing_times[-1] < end:
        raise SystemExit("input sequence must bracket the complete simulation segment")
    if any(right - left != timedelta(hours=1) for left, right in zip(forcing_times, forcing_times[1:])):
        raise SystemExit("input sequence must be continuous at one-hour cadence")

    restart_lines = f"  restartinterval = {args.restart_interval}"
    if args.restart_from:
        if timestamp(args.restart_from) != start:
            raise SystemExit("--restart-from must equal --start-date")
        override_check = ".True." if args.restart_override_check else ".False."
        restart_lines += (
            "\n  restart_run = .True."
            f"\n  restart_date = '{args.restart_from}'"
            f"\n  override_check = {override_check}"
        )
    elif args.restart_override_check:
        raise SystemExit("--restart-override-check requires --restart-from")

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
    rrtmgp_block_stanza = ""
    if args.radiation_scheme == "rrtmgp":
        rrtmgp_block_stanza = (
            "  ! The selected 48-compute-rank national layout uses one RRTMGP block per rank.\n"
            "  rrtmgp_block_N = 256"
        )
    values = {
        "@START_DATE@": args.start_date,
        "@END_DATE@": args.end_date,
        "@NZ@": str(SELECTED_NZ),
        "@LOWEST_LAYER_M@": str(SELECTED_LOWEST_LAYER_M),
        "@ADVECT_DENSITY@": ".True.",
        "@MODEL_DEBUG@": ".True." if args.model_debug else ".False.",
        "@SX@": ".False." if args.disable_sx else ".True.",
        "@ALPHA_CONST@": str(args.alpha_const),
        "@CFL_REDUCTION_FACTOR@": str(args.cfl_reduction_factor),
        "@SNOW_TEMPERATURE_LINE@": snow_temperature_line,
        "@LAND_CLIMATOLOGY_LINES@": "\n".join(land_climatology_lines),
        "@MONTHLY_VEGFRAC@": ".True." if monthly_vegfrac else ".False.",
        "@STATIC_FILE@": str(args.static_file.resolve()),
        "@FORCING_FILE_LIST@": str(args.forcing_file_list.resolve()),
        "@SPARSE_LBC_LINE@": sparse_lbc_line,
        "@OUTPUT_DIR@": f"{args.output_dir.resolve()}/",
        "@RESTART_DIR@": f"{args.restart_dir.resolve()}/",
        "@RESTART_LINES@": restart_lines,
        "@OUTPUT_INTERVAL@": str(args.output_interval),
        "@RADIATION_SCHEME@": args.radiation_scheme,
        "@RADIATION_UPDATE_INTERVAL@": str(args.radiation_update_interval),
        "@RRTMGP_BLOCK_STANZA@": rrtmgp_block_stanza,
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
