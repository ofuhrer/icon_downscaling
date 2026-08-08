#!/usr/bin/env python3
"""Render the national HICAR template only when static and forcing inputs are published."""
import argparse
from datetime import datetime, timedelta
import json
import re
import shutil
import subprocess
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
TEMPLATE = CASE / "config" / "hicar_swiss_200m.nml.in"
TOKEN = re.compile(r"@[A-Z_]+@")


def published(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{label} is missing: {path}")
    marker = Path(f"{path}.ready")
    if not marker.is_file():
        raise SystemExit(f"{label} is not published (missing ready marker): {marker}")


def forcing_timestamp(path: Path) -> datetime:
    try:
        import netCDF4
    except ModuleNotFoundError:
        if not shutil.which("ncdump"):
            raise SystemExit("netCDF4 or ncdump is required to read forcing timestamps")
        header = subprocess.run(
            ["ncdump", "-h", str(path)], check=True, universal_newlines=True, stdout=subprocess.PIPE
        ).stdout
        match = re.search(r'time:units = "([^"]+)"', header)
        if not match:
            raise SystemExit(f"forcing time variable lacks units: {path}")
        units = match.group(1)
        time_dump = subprocess.run(
            ["ncdump", "-v", "time", str(path)],
            check=True, universal_newlines=True, stdout=subprocess.PIPE,
        ).stdout
        assignments = re.findall(r"\btime\s*=\s*(.*?);", time_dump, re.DOTALL)
        values = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", assignments[-1]) if assignments else []
        if not values:
            raise SystemExit(f"forcing file has no time records: {path}")
        unit_match = re.fullmatch(r"(seconds|minutes|hours|days) since (.+)", units)
        if not unit_match:
            raise SystemExit(f"unsupported forcing time units without netCDF4: {units}")
        origin = parse_timestamp(unit_match.group(2).replace("Z", ""))
        return origin + timedelta(**{unit_match.group(1): float(values[-1])})
    with netCDF4.Dataset(path) as ds:
        if "time" not in ds.variables:
            raise SystemExit(f"forcing file lacks time: {path}")
        time = ds.variables["time"]
        if not time.units:
            raise SystemExit(f"forcing time variable lacks units: {path}")
        values = time[:]
        if values.size == 0:
            raise SystemExit(f"forcing file has no time records: {path}")
        timestamp = netCDF4.num2date(values[-1], time.units, calendar=getattr(time, "calendar", "standard"))
    return datetime(timestamp.year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, timestamp.second)


def parse_timestamp(value: str) -> datetime:
    value = value.strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            pass
    raise ValueError(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-file", required=True, type=Path)
    parser.add_argument(
        "--rea-l-land-initialization",
        action="store_true",
        help="Read SWE and snow height from the published static initialization file.",
    )
    parser.add_argument(
        "--depth-varying-soil",
        action="store_true",
        help=(
            "Use the four-layer soil_type_layer field with Noah-MP nmp_opt_soil=2. "
            "This is a controlled sensitivity option, not the production default."
        ),
    )
    parser.add_argument("--forcing-file-list", required=True, type=Path)
    parser.add_argument(
        "--sparse-lbc-file-list",
        type=Path,
        help="published target-grid sparse LBC sequence matching the forcing timestamps",
    )
    parser.add_argument(
        "--forcing-plan",
        type=Path,
        help="Published streaming chunk plan; permits future forcing paths.",
    )
    parser.add_argument("--start-date", required=True, help="HICAR timestamp, e.g. 2026-07-10 18:00:00")
    parser.add_argument("--end-date", required=True, help="HICAR timestamp after start-date")
    parser.add_argument("--output-interval", type=int, default=3600, help="output interval in seconds")
    parser.add_argument(
        "--forcing-interval",
        type=int,
        default=3600,
        help="forcing interval in seconds; production remains 3600",
    )
    parser.add_argument(
        "--radiation-update-interval",
        type=int,
        default=600,
        help="full-radiation update interval in seconds; production remains 600",
    )
    parser.add_argument(
        "--output-profile",
        choices=(
            "routine", "qualification", "mechanism_diagnosis",
            "causal_surface_30min", "terrain_radiation_gate",
            "static_process_case", "land_response_30min", "wind_climatology", "engineering",
        ),
        default="routine",
        help=(
            "Routine production fields, land-surface qualification diagnostics, "
            "mechanism-diagnosis state, "
            "fixed-height wind-climatology fields, or the full start/end "
            "engineering state."
        ),
    )
    parser.add_argument(
        "--terrain-radiation-profile",
        choices=("off", "direct", "direct-diffuse", "full-local", "full-neighborhood"),
        default="off",
        help=(
            "Causal terrain-radiation component set. Production remains off; "
            "non-off profiles require audited hlm, svf, slope_angle, and aspect_angle fields."
        ),
    )
    parser.add_argument("--nz", type=int, default=80,
                        help="vertical levels; supported Swiss candidates are 60 and 80")
    parser.add_argument("--wind-solver-iterations", type=int, default=2500,
                        help="maximum initial variational-wind iterations")
    parser.add_argument("--alpha-const", type=float,
                        help="optional fixed wind-equation alpha (0.01..1.0); omit for dynamic alpha")
    parser.add_argument(
        "--sx",
        choices=("on", "off"),
        default="on",
        help="enable or disable Sx terrain sheltering; defaults to production on",
    )
    parser.add_argument(
        "--advect-density",
        choices=("on", "off"),
        default="on",
        help=(
            "enable or disable density-weighted advection and wind projection; "
            "defaults to production on"
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--restart-dir", required=True, type=Path)
    parser.add_argument(
        "--restart-interval",
        type=int,
        default=0,
        help="Restart interval in output records; zero preserves the no-checkpoint default.",
    )
    parser.add_argument(
        "--restart-from",
        help="Restart timestamp; must equal --start-date when supplied.",
    )
    parser.add_argument(
        "--restart-override-check",
        action="store_true",
        help=(
            "allow a deliberate configuration change across a restart; "
            "intended only for controlled sensitivity experiments"
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        start_time = parse_timestamp(args.start_date)
        end_time = parse_timestamp(args.end_date)
    except ValueError as exc:
        raise SystemExit("start/end dates must be ISO timestamps") from exc
    if end_time <= start_time:
        raise SystemExit("--end-date must be after --start-date")
    if args.output_interval <= 0:
        raise SystemExit("--output-interval must be positive")
    if args.forcing_interval <= 0:
        raise SystemExit("--forcing-interval must be positive")
    if args.radiation_update_interval <= 0:
        raise SystemExit("--radiation-update-interval must be positive")
    if args.output_profile in {"causal_surface_30min", "land_response_30min"} and args.output_interval != 1800:
        raise SystemExit(f"{args.output_profile} requires --output-interval 1800")
    if args.restart_interval < 0:
        raise SystemExit("--restart-interval must be non-negative")
    if args.nz not in (60, 80):
        raise SystemExit("--nz must be one of the validated Swiss candidates: 60 or 80")
    if not 100 <= args.wind_solver_iterations <= 5000:
        raise SystemExit("--wind-solver-iterations must be 100..5000")
    if args.alpha_const is not None and not 0.01 <= args.alpha_const <= 1.0:
        raise SystemExit("--alpha-const must be 0.01..1.0")
    if args.output_profile in {
        "qualification", "mechanism_diagnosis", "causal_surface_30min",
        "static_process_case", "land_response_30min",
    } and not args.rea_l_land_initialization:
        raise SystemExit(
            "selected land/surface process profile requires --rea-l-land-initialization; "
            "cold starts are restricted to engineering smoke profiles"
        )
    published(args.static_file, "static file")
    try:
        import netCDF4
    except ModuleNotFoundError as exc:
        raise SystemExit("Python netCDF4 is required to validate the runtime domain") from exc
    monthly_vegfrac = False
    # Several lightweight renderer tests use an empty published path because
    # they exercise timestamp/template logic only. Real runtime domains are
    # non-empty and receive the full schema inspection here.
    if args.static_file.stat().st_size:
        with netCDF4.Dataset(args.static_file) as static:
            if "VEGFRA" in static.variables:
                vegfrac = static["VEGFRA"]
                if vegfrac.dimensions != ("month", "y", "x") or vegfrac.shape[0] != 12:
                    raise SystemExit("VEGFRA must have dimensions (month, y, x) with 12 months")
                monthly_vegfrac = True
    if args.terrain_radiation_profile != "off":
        try:
            import netCDF4
            import numpy as np
        except ModuleNotFoundError as exc:
            raise SystemExit("terrain-radiation validation requires Python netCDF4 and numpy") from exc
        with netCDF4.Dataset(args.static_file) as static:
            required_attributes = {
                "terrain_radiation_geometry_sha256",
                "terrain_radiation_horizon_convention",
                "terrain_radiation_search_distance_km",
            }
            missing_attributes = sorted(
                name for name in required_attributes if not hasattr(static, name)
            )
            if missing_attributes:
                raise SystemExit(
                    "terrain-radiation static lacks audited attributes: "
                    + ", ".join(missing_attributes)
                )
            if static.terrain_radiation_horizon_convention != "hlm_zenith_angle_degrees_flat_90":
                raise SystemExit("terrain-radiation static uses an unsupported horizon convention")
            missing = sorted({"hlm", "svf", "slope_angle", "aspect_angle"} - set(static.variables))
            if missing:
                raise SystemExit(
                    "terrain-radiation profile requires static variables: " + ", ".join(missing)
                )
            hlm = static.variables["hlm"]
            if hlm.dimensions != ("azimuth", "y", "x") or hlm.shape[0] != 90:
                raise SystemExit("hlm must have NetCDF dimensions (azimuth, y, x) with 90 sectors")
            spatial_shape = hlm.shape[1:]
            for name in ("svf", "slope_angle", "aspect_angle"):
                if static.variables[name].dimensions != ("y", "x") or static.variables[name].shape != spatial_shape:
                    raise SystemExit(f"{name} must have the same (y, x) shape as hlm")
            for name, lower, upper in (
                ("hlm", 0.0, 90.0),
                ("svf", 0.0, 1.0),
                ("slope_angle", 0.0, np.pi / 2.0),
                ("aspect_angle", 0.0, 2.0 * np.pi),
            ):
                values = np.asarray(static.variables[name][:])
                if not np.all(np.isfinite(values)) or np.any(values < lower) or np.any(values > upper):
                    raise SystemExit(f"{name} contains non-finite or out-of-range values")
    if args.depth_varying_soil:
        try:
            import netCDF4
        except ModuleNotFoundError as exc:
            raise SystemExit("--depth-varying-soil requires Python netCDF4 for schema validation") from exc
        with netCDF4.Dataset(args.static_file) as static:
            if "soil_type_layer" not in static.variables:
                raise SystemExit("--depth-varying-soil requires static variable soil_type_layer")
            soil_texture = static.variables["soil_type_layer"]
            if soil_texture.ndim != 3 or soil_texture.shape[0] != 4:
                raise SystemExit(
                    "soil_type_layer must have NetCDF dimensions (soil_layer, y, x) with four layers"
                )
    if not args.forcing_file_list.is_file():
        raise SystemExit(f"forcing file list is missing: {args.forcing_file_list}")
    forcing_list_marker = Path(f"{args.forcing_file_list}.ready")
    if not forcing_list_marker.is_file():
        raise SystemExit(
            f"forcing file list is not published (missing ready marker): "
            f"{forcing_list_marker}"
        )
    forcing_files = [
        Path(line.strip().strip('"'))
        for line in args.forcing_file_list.read_text().splitlines()
        if line.strip()
    ]
    if len(forcing_files) < 2:
        raise SystemExit("forcing file list must contain at least two published timestamps for interpolation")
    if args.forcing_plan:
        published(args.forcing_plan, "forcing plan")
        plan = json.loads(args.forcing_plan.read_text())
        planned_files = [Path(record["forcing_file"]) for record in plan["records"]]
        if forcing_files != planned_files:
            raise SystemExit("forcing file list does not exactly match the streaming plan")
        timestamps = [
            parse_timestamp(record["valid_time"]) for record in plan["records"]
        ]
        if plan.get("start") != timestamps[0].strftime("%Y-%m-%dT%H:%M:%S"):
            raise SystemExit("streaming plan start disagrees with its first record")
        if plan.get("end") != timestamps[-1].strftime("%Y-%m-%dT%H:%M:%S"):
            raise SystemExit("streaming plan end disagrees with its final record")
    else:
        for path in forcing_files:
            published(path, "forcing file")
        timestamps = [forcing_timestamp(path) for path in forcing_files]
    if timestamps != sorted(timestamps):
        raise SystemExit("forcing file list timestamps are not monotonic")
    if timestamps[0] != start_time:
        raise SystemExit("forcing file list must start exactly at --start-date")
    expected_gap = timedelta(seconds=args.forcing_interval)
    if any(right - left != expected_gap for left, right in zip(timestamps, timestamps[1:])):
        raise SystemExit("forcing file list is not continuous at the configured 3600 s interval")
    if timestamps[-1] < end_time:
        raise SystemExit("forcing file list lacks a record at or after --end-date")
    sparse_lbc_line = ""
    if args.sparse_lbc_file_list is not None:
        published(args.sparse_lbc_file_list, "sparse LBC file list")
        sparse_files = [
            Path(line.strip().strip('"'))
            for line in args.sparse_lbc_file_list.read_text().splitlines()
            if line.strip()
        ]
        if len(sparse_files) < 2:
            raise SystemExit("sparse LBC file list must contain at least two frames")
        sparse_times = []
        for path in sparse_files:
            published(path, "sparse LBC frame")
            with netCDF4.Dataset(path) as frame:
                if getattr(frame, "product_type", "") != "hicar_lateral_boundary_state":
                    raise SystemExit(f"not a HICAR sparse LBC frame: {path}")
                try:
                    sparse_times.append(parse_timestamp(str(frame.valid_time).replace("Z", "")))
                except (AttributeError, ValueError) as exc:
                    raise SystemExit(f"sparse LBC frame has invalid valid_time: {path}") from exc
        if sparse_times != timestamps:
            raise SystemExit("sparse LBC timestamps must exactly match forcing timestamps")
        sparse_lbc_line = (
            f"  sparse_lbc_file_list = '{args.sparse_lbc_file_list.resolve()}'"
        )
    restart_lines = ""
    if args.restart_interval:
        restart_lines += f"  restartinterval = {args.restart_interval}\n"
    if args.restart_from:
        try:
            restart_time = parse_timestamp(args.restart_from)
        except ValueError as exc:
            raise SystemExit("--restart-from must be an ISO timestamp") from exc
        if restart_time != start_time:
            raise SystemExit("--restart-from must equal --start-date")
        restart_lines += (
            "  restart_run = .True.\n"
            f"  restart_date = '{args.restart_from}'\n"
            f"  override_check = "
            f"{'.True.' if args.restart_override_check else '.False.'}\n"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.restart_dir.mkdir(parents=True, exist_ok=True)
    rendered = TEMPLATE.read_text()
    terrain_profiles = {
        "off": (False, False, False, False, False, 0.0),
        "direct": (True, True, False, False, False, 0.0),
        "direct-diffuse": (True, True, True, False, False, 0.0),
        "full-local": (True, True, True, True, True, 0.0),
        "full-neighborhood": (True, True, True, True, True, 1500.0),
    }
    terrain_master, terrain_direct, terrain_diffuse, terrain_reflected, terrain_lw, terrain_radius = (
        terrain_profiles[args.terrain_radiation_profile]
    )
    terrain_radiation_lines = "\n".join(
        (
            f"  terrain_shading = {'.True.' if terrain_master else '.False.'}",
            f"  terrain_direct_sw = {'.True.' if terrain_direct else '.False.'}",
            f"  terrain_diffuse_sw = {'.True.' if terrain_diffuse else '.False.'}",
            f"  terrain_reflected_sw = {'.True.' if terrain_reflected else '.False.'}",
            f"  terrain_longwave = {'.True.' if terrain_lw else '.False.'}",
            f"  terrain_refl_radius = {terrain_radius:.1f}",
        )
    )
    terrain_output_variables = (
        ", 'swtr', 'shortwave_direct_horizontal', 'shortwave_diffuse_horizontal'"
        if terrain_master else ""
    )
    output_variables = {
        "routine": (
            "'precipitation', 'psfc', 'taix', 'hus2m', "
            "'u10m', 'v10m', 'rsds', 'lwtr', 'rlus', 'hfgs', 'emiss'"
        ),
        "qualification": (
            "'precipitation', 'snowfall', 'graupel', 'psfc', 'taix', 'hus2m', "
            "'u10m', 'v10m', 'rsds', 'lwtr', 'rlus', 'hfgs', 'hfss', 'hfls', "
            "'emiss', 'tsfe', 'albedo', 'canopy_water', 'swet', 'snow_height', "
            "'soil_column_total_water', 'soil_water_content', 'soil_temperature', "
            "'runoff_surface', 'runoff_subsurface', "
            "'runoff_surface_cumulative', 'runoff_subsurface_cumulative', "
            "'evaporation_net_cumulative', 'water_aquifer', 'storage_gw', "
            "'wetland_h20_store'"
        ),
        "mechanism_diagnosis": (
            "'precipitation', 'snowfall', 'graupel', 'psfc', 'taix', 'hus2m', "
            "'rsds', 'swtb', 'swtd'" + terrain_output_variables + ", "
            "'swcf', 'lwtr', 'rlus', 'lwcf', "
            "'tend_th_swrad', 'tend_th_lwrad', 'cldfrac', 'qc', 'qi', 'qr', "
            "'qs', 'qg', 'hfss', 'hfls', 'tsfe', 'albedo', 'snow_height', "
            "'soil_column_total_water', 'soil_water_content', 'soil_temperature'"
        ),
        # Bounded V29 causal instrumentation: 2-D-only fields at a fixed
        # 30-minute cadence.  It preserves V29 physics but avoids the five
        # 80-level hydrometeor histories that would make time integration
        # impracticable on the national domain.
        "causal_surface_30min": (
            "'precipitation', 'snowfall', 'graupel', 'psfc', 'taix', 'hus2m', "
            "'rsds', 'swtb', 'swtd'" + terrain_output_variables + ", "
            "'swcf', 'lwtr', 'rlus', 'lwcf', "
            "'cldfrac', 'hfss', 'hfls', 'tsfe', 'albedo', "
            "'soil_column_total_water'"
        ),
        # Small synthetic causal gate. Five-minute radiation/output cadence
        # resolves the deliberately narrow 4-degree blocked azimuth sector;
        # no 3-D histories are needed for this component/restart test.
        "terrain_radiation_gate": (
            "'rsds', 'swtb', 'swtd', 'swtr', "
            "'shortwave_direct_horizontal', 'shortwave_diffuse_horizontal', "
            "'cosz', 'lwtr', 'tsfe', 'hfss', 'hfls'"
        ),
        "static_process_case": (
            "'precipitation', 'snowfall', 'psfc', 'taix', 'hus2m', "
            "'u10m', 'v10m', 'u_agl', 'v_agl', 'rho_agl', 'ustar', "
            "'surface_roughness', 'sfc_Ri', 'hpbl', "
            "'rsds', 'lwtr', 'rlus', 'hfgs', 'hfss', 'hfls', "
            "'tsfe', 'albedo', 'snow_height', 'soil_column_total_water', "
            "'soil_water_content', 'soil_temperature'"
        ),
        # Compact matched cold-start response profile.  Keep the liquid-water
        # partition explicitly so total-minus-liquid soil ice can be audited.
        "land_response_30min": (
            "'precipitation', 'psfc', 'taix', 'hus2m', 'u10m', 'v10m', 'hpbl', "
            "'hfss', 'hfls', 'tsfe', 'swet', 'snow_height', "
            "'soil_column_total_water', 'soil_water_content', "
            "'soil_water_content_liq', 'soil_temperature'"
        ),
        "wind_climatology": (
            "'u10m', 'v10m', 'u_agl', 'v_agl', 'rho_agl', "
            "'ustar', 'surface_roughness', 'sfc_Ri', 'hpbl'"
        ),
        "engineering": (
            "'u', 'v', 'w', 'w_grid', 'pressure', 'temperature', "
            "'potential_temperature', 'qv', 'qc', 'qi', 'qr', 'qs', 'qg', "
            "'density', 'z', 'z_i', "
            "'jacobian', 'precipitation', 'psfc', 'psl'"
        ),
    }
    values = {
        "@START_DATE@": args.start_date,
        "@END_DATE@": args.end_date,
        "@STATIC_FILE@": str(args.static_file.resolve()),
        "@FORCING_FILE_LIST@": str(args.forcing_file_list.resolve()),
        "@SPARSE_LBC_LINE@": sparse_lbc_line,
        "@OUTPUT_DIR@": f"{args.output_dir.resolve()}/",
        "@RESTART_DIR@": f"{args.restart_dir.resolve()}/",
        "@RESTART_LINES@": restart_lines.rstrip(),
        "@SNOW_INIT_LINES@": (
            "  swe_var = 'swe'\n  snowh_var = 'snow_height'"
            if args.rea_l_land_initialization
            else ""
        ),
        "@TERRAIN_RADIATION_DOMAIN_LINES@": (
            "  svf_var = 'svf'\n"
            "  hlm_var = 'hlm'\n"
            "  slope_angle_var = 'slope_angle'\n"
            "  aspect_angle_var = 'aspect_angle'"
            if terrain_master else ""
        ),
        "@TERRAIN_RADIATION_LINES@": terrain_radiation_lines,
        "@SOIL_TEXTURE_LINE@": (
            "  soiltexture_var = 'soil_type_layer'" if args.depth_varying_soil else ""
        ),
        "@VEGETATION_DOMAIN_LINES@": (
            "  vegfrac_var = 'VEGFRA'" if monthly_vegfrac else ""
        ),
        "@VEGETATION_LSM_LINES@": (
            "  monthly_vegfrac = .True." if monthly_vegfrac else ""
        ),
        "@NMP_OPT_SOIL@": "2" if args.depth_varying_soil else "1",
        "@OUTPUT_INTERVAL@": str(args.output_interval),
        "@FORCING_INTERVAL@": str(args.forcing_interval),
        "@RADIATION_UPDATE_INTERVAL@": str(args.radiation_update_interval),
        "@OUTPUT_VARS@": output_variables[args.output_profile],
        "@NZ@": str(args.nz),
        "@WIND_SOLVER_ITERATIONS@": str(args.wind_solver_iterations),
        "@SX_ENABLED@": ".True." if args.sx == "on" else ".False.",
        "@ADVECT_DENSITY@": (
            ".True." if args.advect_density == "on" else ".False."
        ),
        "@ALPHA_CONST_LINE@": (
            # Freeze the effective production-pin default.  A missing value is
            # otherwise version-dependent and the V29 baseline used 1.0.
            f"  alpha_const = {args.alpha_const if args.alpha_const is not None else 1.0}\n"
        ),
    }
    for token, value in values.items():
        rendered = rendered.replace(token, value)
    if TOKEN.search(rendered):
        raise SystemExit("unresolved template token")
    args.output.write_text(rendered)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
