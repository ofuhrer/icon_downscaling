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
    parser.add_argument("--forcing-file-list", required=True, type=Path)
    parser.add_argument(
        "--forcing-plan",
        type=Path,
        help="Published streaming chunk plan; permits future forcing paths.",
    )
    parser.add_argument("--start-date", required=True, help="HICAR timestamp, e.g. 2026-07-10 18:00:00")
    parser.add_argument("--end-date", required=True, help="HICAR timestamp after start-date")
    parser.add_argument("--output-interval", type=int, default=3600, help="output interval in seconds")
    parser.add_argument(
        "--output-profile",
        choices=("routine", "qualification", "wind_climatology", "engineering"),
        default="routine",
        help=(
            "Routine production fields, land-surface qualification diagnostics, "
            "fixed-height wind-climatology fields, or the full start/end "
            "engineering state."
        ),
    )
    parser.add_argument("--nz", type=int, default=80,
                        help="vertical levels; supported Swiss candidates are 60 and 80")
    parser.add_argument("--wind-solver-iterations", type=int, default=2500,
                        help="maximum initial variational-wind iterations")
    parser.add_argument("--alpha-const", type=float,
                        help="optional fixed wind-equation alpha (0.01..1.0); omit for dynamic alpha")
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
    if args.restart_interval < 0:
        raise SystemExit("--restart-interval must be non-negative")
    if args.nz not in (60, 80):
        raise SystemExit("--nz must be one of the validated Swiss candidates: 60 or 80")
    if not 100 <= args.wind_solver_iterations <= 5000:
        raise SystemExit("--wind-solver-iterations must be 100..5000")
    if args.alpha_const is not None and not 0.01 <= args.alpha_const <= 1.0:
        raise SystemExit("--alpha-const must be 0.01..1.0")
    published(args.static_file, "static file")
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
    expected_gap = timedelta(seconds=3600)
    if any(right - left != expected_gap for left, right in zip(timestamps, timestamps[1:])):
        raise SystemExit("forcing file list is not continuous at the configured 3600 s interval")
    if timestamps[-1] < end_time:
        raise SystemExit("forcing file list lacks a record at or after --end-date")
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
            "  override_check = .False.\n"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.restart_dir.mkdir(parents=True, exist_ok=True)
    rendered = TEMPLATE.read_text()
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
        "wind_climatology": (
            "'u10m', 'v10m', 'u_agl', 'v_agl', 'rho_agl', "
            "'ustar', 'surface_roughness', 'sfc_Ri', 'hpbl'"
        ),
        "engineering": (
            "'u', 'v', 'w', 'w_grid', 'pressure', 'temperature', 'qv', "
            "'density', 'z', 'jacobian', 'precipitation', 'psfc', 'psl'"
        ),
    }
    values = {
        "@START_DATE@": args.start_date,
        "@END_DATE@": args.end_date,
        "@STATIC_FILE@": str(args.static_file.resolve()),
        "@FORCING_FILE_LIST@": str(args.forcing_file_list.resolve()),
        "@OUTPUT_DIR@": f"{args.output_dir.resolve()}/",
        "@RESTART_DIR@": f"{args.restart_dir.resolve()}/",
        "@RESTART_LINES@": restart_lines.rstrip(),
        "@SNOW_INIT_LINES@": (
            "  swe_var = 'swe'\n  snowh_var = 'snow_height'"
            if args.rea_l_land_initialization
            else ""
        ),
        "@OUTPUT_INTERVAL@": str(args.output_interval),
        "@OUTPUT_VARS@": output_variables[args.output_profile],
        "@NZ@": str(args.nz),
        "@WIND_SOLVER_ITERATIONS@": str(args.wind_solver_iterations),
        "@ALPHA_CONST_LINE@": (
            f"  alpha_const = {args.alpha_const}\n" if args.alpha_const is not None else ""
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
