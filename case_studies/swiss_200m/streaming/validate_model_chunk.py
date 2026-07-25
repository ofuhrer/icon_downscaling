#!/usr/bin/env python3
"""Validate a completed HICAR stream chunk and publish its completion manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


ROUTINE_VARIABLES = (
    "precipitation",
    "psfc",
    "taix",
    "hus2m",
    "u10m",
    "v10m",
    "rsds",
    "lwtr",
    "rlus",
    "hfgs",
    "emiss",
)

QUALIFICATION_VARIABLES = ROUTINE_VARIABLES + (
    "snowfall",
    "graupel",
    "hfss",
    "hfls",
    "tsfe",
    "albedo",
    "canopy_water",
    "swet",
    "snow_height",
    "soil_column_total_water",
    "soil_water_content",
    "soil_temperature",
    "runoff_surface",
    "runoff_subsurface",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
    "water_aquifer",
    "storage_gw",
    "wetland_h20_store",
)

WIND_CLIMATOLOGY_VARIABLES = (
    "u10m",
    "v10m",
    "u_agl",
    "v_agl",
    "rho_agl",
    "ustar",
    "surface_roughness",
    "sfc_Ri",
    "hpbl",
)

OUTPUT_PROFILES = {
    "routine": ROUTINE_VARIABLES,
    "qualification": QUALIFICATION_VARIABLES,
    "wind_climatology": WIND_CLIMATOLOGY_VARIABLES,
}

QUALIFICATION_LIMITS = {
    "precipitation": (-1.0e-6, 10000.0),
    # Accumulated surface amounts in kg m-2, not instantaneous rates.
    # Event plausibility is assessed by the independent precipitation
    # diagnostics; this is a broad, duration-independent corruption screen.
    "snowfall": (-1.0e-6, 10000.0),
    "graupel": (-1.0e-6, 10000.0),
    "psfc": (20000.0, 120000.0),
    "taix": (180.0, 340.0),
    "hus2m": (0.0, 0.1),
    "u10m": (-200.0, 200.0),
    "v10m": (-200.0, 200.0),
    "rsds": (0.0, 2000.0),
    "lwtr": (0.0, 1000.0),
    "rlus": (0.0, 1000.0),
    "hfgs": (-5000.0, 5000.0),
    "hfss": (-5000.0, 5000.0),
    "hfls": (-5000.0, 5000.0),
    "emiss": (0.0, 1.1),
    "tsfe": (180.0, 340.0),
    "albedo": (0.0, 1.1),
    "canopy_water": (0.0, 100.0),
    "swet": (0.0, 5000.0),
    "snow_height": (0.0, 20.0),
    "soil_column_total_water": (0.0, 1200.0),
    "soil_water_content": (0.0, 0.8),
    "soil_temperature": (180.0, 340.0),
    "runoff_surface": (-1.0e-10, 100.0),
    "runoff_subsurface": (-1.0e-10, 100.0),
    "runoff_surface_cumulative": (-1.0e-6, 10000.0),
    "runoff_subsurface_cumulative": (-1.0e-6, 10000.0),
    "evaporation_net_cumulative": (-10000.0, 10000.0),
    "water_aquifer": (0.0, 10000.0),
    "storage_gw": (0.0, 20000.0),
    "wetland_h20_store": (0.0, 5000.0),
}

SOIL_QUALIFICATION_VARIABLES = {
    "soil_column_total_water",
    "soil_water_content",
    "soil_temperature",
    "runoff_surface",
    "runoff_subsurface",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
    "water_aquifer",
    "storage_gw",
    "wetland_h20_store",
}

PRODUCTION_CUMULATIVE_WATER_VARIABLES = {
    "precipitation",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
    "evaporation_net_cumulative",
}
NONDECREASING_CUMULATIVE_WATER_VARIABLES = {
    "precipitation",
    "runoff_surface_cumulative",
    "runoff_subsurface_cumulative",
}
WATER_ACCUMULATION_SEMANTICS = (
    "cumulative since simulation start; no output reset; restart-persistent"
)

WIND_HEIGHTS_AGL_M = np.asarray(
    (50.0, 75.0, 100.0, 125.0, 150.0, 200.0),
    dtype=np.float64,
)

WIND_CLIMATOLOGY_LIMITS = {
    "u10m": (-200.0, 200.0),
    "v10m": (-200.0, 200.0),
    "u_agl": (-200.0, 200.0),
    "v_agl": (-200.0, 200.0),
    "rho_agl": (0.2, 1.7),
    "ustar": (0.0, 20.0),
    "surface_roughness": (0.0, 100.0),
    # The MM5-revised surface layer stores the raw bulk Richardson number.
    # It clips only the value used by the similarity-function inversion
    # (SBRLIM=250), so calm stable cells can legitimately exceed 250.
    "sfc_Ri": (-1.0e4, 1.0e4),
    "hpbl": (0.0, 20000.0),
}

WIND_SURFACE_VARIABLES = {
    "u10m",
    "v10m",
    "ustar",
    "surface_roughness",
    "sfc_Ri",
    "hpbl",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def timestamps(path: Path) -> list[datetime]:
    with netCDF4.Dataset(path) as dataset:
        variable = dataset.variables["time"]
        values = netCDF4.num2date(
            variable[:],
            variable.units,
            calendar=getattr(variable, "calendar", "standard"),
        )
    return [
        datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
        for value in values
    ]


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


def validate_provenance(args, failures: list[str]) -> dict:
    """Validate the frozen model inputs and executable used by the runner."""
    paths = {
        "source_commit": args.source_commit_file,
        "source_tree_status": args.source_tree_status_file,
        "executable": args.executable,
        "executable_digest": args.executable_digest_file,
        "forcing_publication": args.forcing_publication,
        "archived_plan": args.archived_plan,
        "archived_forcing_publication": args.archived_forcing_publication,
    }
    supplied = [value is not None for value in paths.values()]
    if not any(supplied):
        return {
            "status": "NOT_REQUESTED",
            "interpretation": (
                "Legacy validation without the production provenance contract."
            ),
        }
    if not all(supplied):
        missing = sorted(name for name, value in paths.items() if value is None)
        failures.append(
            "production provenance arguments are all-or-none; missing "
            + ", ".join(missing)
        )
        return {"status": "FAIL", "missing_arguments": missing}

    provenance_failures: list[str] = []

    def fail(message: str) -> None:
        provenance_failures.append(message)
        failures.append(f"provenance: {message}")

    for name, path in paths.items():
        if name == "source_tree_status":
            if not path.is_file():
                fail(f"missing {name}: {path}")
        elif not path.is_file() or path.stat().st_size == 0:
            fail(f"missing or empty {name}: {path}")

    source_commit = None
    if paths["source_commit"].is_file():
        source_commit = paths["source_commit"].read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit):
            fail("source commit is not a full hexadecimal object id")

    source_tree_status = None
    if paths["source_tree_status"].is_file():
        source_tree_status = paths["source_tree_status"].read_text()
        if source_tree_status:
            fail("source tree was not clean before execution")

    recorded_executable_sha256 = None
    executable_sha256 = None
    if paths["executable_digest"].is_file():
        fields = paths["executable_digest"].read_text().split()
        if not fields or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            fail("executable digest file does not contain a SHA-256 digest")
        else:
            recorded_executable_sha256 = fields[0]
    if paths["executable"].is_file():
        executable_sha256 = sha256(paths["executable"])
        if (
            recorded_executable_sha256 is not None
            and executable_sha256 != recorded_executable_sha256
        ):
            fail("executable no longer matches its pre-run SHA-256 digest")

    plan_sha256 = sha256(args.plan)
    archived_plan_sha256 = (
        sha256(paths["archived_plan"]) if paths["archived_plan"].is_file() else None
    )
    if archived_plan_sha256 is not None and archived_plan_sha256 != plan_sha256:
        fail("archived chunk plan differs from the published source plan")

    forcing_publication_sha256 = (
        sha256(paths["forcing_publication"])
        if paths["forcing_publication"].is_file()
        else None
    )
    archived_forcing_publication_sha256 = (
        sha256(paths["archived_forcing_publication"])
        if paths["archived_forcing_publication"].is_file()
        else None
    )
    if (
        forcing_publication_sha256 is not None
        and archived_forcing_publication_sha256 is not None
        and forcing_publication_sha256 != archived_forcing_publication_sha256
    ):
        fail("archived forcing publication differs from the published source")
    if paths["forcing_publication"].is_file():
        try:
            forcing = json.loads(paths["forcing_publication"].read_text())
            if forcing.get("status") != "PASS":
                fail("forcing publication status is not PASS")
        except Exception as exc:
            fail(f"cannot read forcing publication: {exc}")
    forcing_ready = Path(f"{paths['forcing_publication']}.ready")
    if not forcing_ready.is_file():
        fail("forcing publication ready marker is missing")

    static_sha256 = (
        sha256(args.static_file)
        if args.static_file is not None and args.static_file.is_file()
        else None
    )
    if args.static_file is None or static_sha256 is None:
        fail("production provenance requires a readable static domain")

    return {
        "status": "PASS" if not provenance_failures else "FAIL",
        "source_commit": source_commit,
        "source_commit_file": str(paths["source_commit"].resolve()),
        "source_tree_clean": source_tree_status == "",
        "source_cleanliness_scope": (
            "all tracked files plus untracked files under src, cmake, external, "
            "tools, CMakeLists.txt, and CMakePresets.json; unrelated untracked "
            "runtime artifacts are permitted"
        ),
        "source_tree_status_file": str(paths["source_tree_status"].resolve()),
        "executable": str(paths["executable"].resolve()),
        "executable_sha256": executable_sha256,
        "recorded_executable_sha256": recorded_executable_sha256,
        "plan_sha256": plan_sha256,
        "archived_plan": str(paths["archived_plan"].resolve()),
        "archived_plan_sha256": archived_plan_sha256,
        "forcing_publication": str(paths["forcing_publication"].resolve()),
        "forcing_publication_sha256": forcing_publication_sha256,
        "archived_forcing_publication": str(
            paths["archived_forcing_publication"].resolve()
        ),
        "archived_forcing_publication_sha256": (
            archived_forcing_publication_sha256
        ),
        "static_file": str(args.static_file.resolve()) if args.static_file else None,
        "static_sha256": static_sha256,
        "failures": provenance_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--static-file",
        type=Path,
        help="Static domain supplying landmask for qualification data checks.",
    )
    parser.add_argument("--output-file", required=True, type=Path, nargs="+")
    parser.add_argument("--restart-file", required=True, type=Path)
    parser.add_argument("--model-log", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-commit-file", type=Path)
    parser.add_argument("--source-tree-status-file", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--executable-digest-file", type=Path)
    parser.add_argument("--forcing-publication", type=Path)
    parser.add_argument("--archived-plan", type=Path)
    parser.add_argument("--archived-forcing-publication", type=Path)
    parser.add_argument(
        "--output-profile",
        choices=tuple(OUTPUT_PROFILES),
        default="routine",
        help="Required output-variable contract.",
    )
    parser.add_argument(
        "--output-interval-seconds",
        type=int,
        default=3600,
        help="Expected interval between output records.",
    )
    parser.add_argument(
        "--restart-continuation",
        action="store_true",
        help="Expect output after, rather than at, the already-published start boundary.",
    )
    args = parser.parse_args()

    plan = json.loads(args.plan.read_text())
    start = datetime.fromisoformat(plan["start"])
    end = datetime.fromisoformat(plan["end"])
    failures: list[str] = []
    duration_seconds = int((end - start).total_seconds())
    if args.output_interval_seconds <= 0:
        failures.append("output interval must be positive")
    elif duration_seconds % args.output_interval_seconds:
        failures.append(
            "chunk duration is not divisible by the configured output interval"
        )
    required_variables = OUTPUT_PROFILES[args.output_profile]
    landmask = None
    active_soil_mask = None
    if args.output_profile == "qualification":
        if args.static_file is None:
            failures.append("qualification validation requires --static-file")
        elif not args.static_file.is_file():
            failures.append(f"static file is missing: {args.static_file}")
        else:
            try:
                with netCDF4.Dataset(args.static_file) as static:
                    landmask = np.asarray(static.variables["landmask"][:]) > 0
                    landuse = np.asarray(static.variables["landuse"][:])
                if landmask.ndim != 2 or not np.any(landmask):
                    failures.append("static landmask is not a nonempty 2-D field")
                elif landuse.shape != landmask.shape:
                    failures.append("static landuse does not match landmask")
                else:
                    # The Swiss configuration uses USGS classes. Noah-MP
                    # handles class 16 as water and class 24 as permanent
                    # snow/ice; their saturated pseudo-soil state is not a
                    # meaningful soil-hydrology range check.
                    active_soil_mask = landmask & (landuse != 16) & (landuse != 24)
                    if not np.any(active_soil_mask):
                        failures.append("static active-soil mask is empty")
            except Exception as exc:
                failures.append(f"cannot read static surface classes: {exc}")
    for path in (*args.output_file, args.restart_file, args.model_log):
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"missing or empty artifact: {path}")

    output_times: list[datetime] = []
    output_variables: list[str] = []
    output_artifacts: list[dict] = []
    output_ranges: dict[str, list[float]] = {}
    for output_file in args.output_file:
        if not output_file.is_file() or not output_file.stat().st_size:
            continue
        try:
            with netCDF4.Dataset(output_file) as dataset:
                variables = sorted(dataset.variables)
                if args.output_profile == "qualification":
                    for variable in required_variables:
                        if variable not in dataset.variables:
                            continue
                        field = dataset.variables[variable]
                        if variable in PRODUCTION_CUMULATIVE_WATER_VARIABLES:
                            if getattr(field, "units", None) != "kg m-2":
                                failures.append(
                                    f"qualification output {output_file} {variable} "
                                    "lacks units=kg m-2"
                                )
                            if (
                                getattr(field, "accumulation_semantics", None)
                                != WATER_ACCUMULATION_SEMANTICS
                            ):
                                failures.append(
                                    f"qualification output {output_file} {variable} "
                                    "lacks no-reset restart-persistent semantics"
                                )
                            if "(previous_time, time]" not in getattr(
                                field, "interval_semantics", ""
                            ):
                                failures.append(
                                    f"qualification output {output_file} {variable} "
                                    "lacks exact interval semantics"
                                )
                        values = np.ma.asarray(field[:])
                        variable_mask = (
                            active_soil_mask
                            if variable in SOIL_QUALIFICATION_VARIABLES
                            else landmask
                        )
                        if variable_mask is not None:
                            if values.shape[-2:] != variable_mask.shape:
                                failures.append(
                                    f"qualification output {output_file} {variable} "
                                    "does not match static surface mask"
                                )
                                continue
                            values = values[..., variable_mask]
                        if np.ma.count_masked(values):
                            failures.append(
                                f"qualification output {output_file} has masked land {variable}"
                            )
                            continue
                        raw = np.asarray(values)
                        if not np.all(np.isfinite(raw)):
                            failures.append(
                                f"qualification output {output_file} has non-finite land {variable}"
                            )
                            continue
                        if (
                            variable
                            in NONDECREASING_CUMULATIVE_WATER_VARIABLES
                            and raw.shape[0] > 1
                            and np.any(np.diff(raw, axis=0) < -1.0e-6)
                        ):
                            failures.append(
                                f"qualification output {output_file} {variable} "
                                "decreases within the chunk"
                            )
                        local_range = [float(np.min(raw)), float(np.max(raw))]
                        lower, upper = QUALIFICATION_LIMITS[variable]
                        if local_range[0] < lower or local_range[1] > upper:
                            failures.append(
                                f"qualification land {variable} range "
                                f"{local_range[0]}..{local_range[1]} is outside "
                                f"{lower}..{upper}"
                            )
                        if variable in output_ranges:
                            output_ranges[variable][0] = min(
                                output_ranges[variable][0], local_range[0]
                            )
                            output_ranges[variable][1] = max(
                                output_ranges[variable][1], local_range[1]
                            )
                        else:
                            output_ranges[variable] = local_range
                if args.output_profile == "wind_climatology":
                    if "height_agl" not in dataset.variables:
                        failures.append(
                            f"wind_climatology output {output_file} is missing height_agl"
                        )
                    else:
                        height = dataset.variables["height_agl"]
                        values = np.ma.asarray(height[:])
                        if (
                            height.dimensions != ("height_agl",)
                            or values.shape != WIND_HEIGHTS_AGL_M.shape
                            or np.ma.count_masked(values)
                            or not np.all(np.isfinite(np.asarray(values)))
                            or not np.allclose(
                                np.asarray(values),
                                WIND_HEIGHTS_AGL_M,
                                rtol=0.0,
                                atol=1.0e-6,
                            )
                        ):
                            failures.append(
                                f"wind_climatology output {output_file} has invalid "
                                "height_agl coordinate"
                            )
                        for attribute, expected in (
                            ("standard_name", "height"),
                            ("units", "m"),
                            ("positive", "up"),
                            ("axis", "Z"),
                        ):
                            if getattr(height, attribute, None) != expected:
                                failures.append(
                                    f"wind_climatology output {output_file} height_agl "
                                    f"lacks {attribute}={expected}"
                                )
                    expected_metadata = {
                        "u10m": ("eastward_wind", "m s-1"),
                        "v10m": ("northward_wind", "m s-1"),
                        "u_agl": ("eastward_wind", "m s-1"),
                        "v_agl": ("northward_wind", "m s-1"),
                        "rho_agl": ("air_density", "kg m-3"),
                        "ustar": (
                            "magnitude_of_surface_friction_velocity_in_air",
                            "m s-1",
                        ),
                        "surface_roughness": (
                            "surface_roughness_length_for_momentum_in_air",
                            "m",
                        ),
                        "sfc_Ri": (None, "1"),
                        "hpbl": (
                            "atmosphere_boundary_layer_thickness",
                            "m",
                        ),
                    }
                    for variable in required_variables:
                        if variable not in dataset.variables:
                            continue
                        field = dataset.variables[variable]
                        if variable in expected_metadata:
                            expected_dimensions = (
                                ("time", "lat_y", "lon_x")
                                if variable in WIND_SURFACE_VARIABLES
                                else (
                                    "time",
                                    "height_agl",
                                    "lat_y",
                                    "lon_x",
                                )
                            )
                            if field.dimensions != expected_dimensions:
                                failures.append(
                                    f"wind_climatology output {output_file} {variable} "
                                    f"has dimensions {field.dimensions}"
                            )
                            standard_name, units = expected_metadata[variable]
                            if (
                                standard_name is not None
                                and getattr(field, "standard_name", None)
                                != standard_name
                            ):
                                failures.append(
                                    f"wind_climatology output {output_file} {variable} "
                                    f"lacks standard_name={standard_name}"
                                )
                            if getattr(field, "units", None) != units:
                                failures.append(
                                    f"wind_climatology output {output_file} {variable} "
                                    f"lacks units={units}"
                                )
                            if variable in {"u_agl", "v_agl", "rho_agl"} and (
                                getattr(field, "interpolation", None)
                                != "linear in geometric height AGL; no extrapolation"
                            ):
                                failures.append(
                                    f"wind_climatology output {output_file} {variable} "
                                    "lacks fixed-height interpolation metadata"
                                )
                        values = np.ma.asarray(field[:])
                        if np.ma.count_masked(values):
                            failures.append(
                                f"wind_climatology output {output_file} has masked {variable}"
                            )
                            continue
                        raw = np.asarray(values)
                        if not np.all(np.isfinite(raw)):
                            failures.append(
                                f"wind_climatology output {output_file} has non-finite "
                                f"{variable}"
                            )
                            continue
                        local_range = [float(np.min(raw)), float(np.max(raw))]
                        lower, upper = WIND_CLIMATOLOGY_LIMITS[variable]
                        if local_range[0] < lower or local_range[1] > upper:
                            failures.append(
                                f"wind_climatology {variable} range "
                                f"{local_range[0]}..{local_range[1]} is outside "
                                f"{lower}..{upper}"
                            )
                        if variable in output_ranges:
                            output_ranges[variable][0] = min(
                                output_ranges[variable][0], local_range[0]
                            )
                            output_ranges[variable][1] = max(
                                output_ranges[variable][1], local_range[1]
                            )
                        else:
                            output_ranges[variable] = local_range
            file_times = timestamps(output_file)
            for variable in required_variables:
                if variable not in variables:
                    failures.append(
                        f"{args.output_profile} output {output_file} is missing {variable}"
                    )
            if args.output_profile == "routine" and "z" in variables:
                failures.append(
                    f"{args.output_profile} output {output_file} contains static 3-D z; "
                    "restart-only state must not inflate history files"
                )
            output_variables = sorted(set(output_variables) | set(variables))
            output_times.extend(file_times)
            output_artifacts.append(
                {
                    "path": str(output_file.resolve()),
                    "size_bytes": output_file.stat().st_size,
                    "sha256": sha256(output_file),
                    "times": [value.isoformat() for value in file_times],
                }
            )
        except Exception as exc:
            failures.append(f"cannot validate output file {output_file}: {exc}")
    interval = timedelta(seconds=args.output_interval_seconds)
    expected_start = start + interval if args.restart_continuation else start
    expected_count = (
        duration_seconds // args.output_interval_seconds
        if args.output_interval_seconds > 0
        and duration_seconds % args.output_interval_seconds == 0
        else -1
    )
    if not args.restart_continuation and expected_count >= 0:
        expected_count += 1
    if (
        not output_times
        or output_times[0] != expected_start
        or output_times[-1] != end
        or len(output_times) != expected_count
        or output_times != sorted(set(output_times))
        or any(right - left != interval for left, right in zip(output_times, output_times[1:]))
    ):
        failures.append(
            f"output time coverage is not unique and monotonic over "
            f"{expected_start.isoformat()}..{end.isoformat()}"
        )

    restart_times: list[datetime] = []
    restart_variables: list[str] = []
    restart_dt_seconds = None
    if args.restart_file.is_file() and args.restart_file.stat().st_size:
        try:
            with netCDF4.Dataset(args.restart_file) as dataset:
                restart_variables = sorted(dataset.variables)
                restart_dt_seconds = getattr(dataset, "dt_seconds", None)
                if restart_dt_seconds is not None:
                    restart_dt_seconds = float(restart_dt_seconds)
            restart_times = timestamps(args.restart_file)
            if restart_times != [end]:
                failures.append(
                    f"restart time is not exactly the chunk end {end.isoformat()}"
                )
            if restart_dt_seconds is None or float(restart_dt_seconds) <= 0:
                failures.append("restart file lacks a positive dt_seconds attribute")
        except Exception as exc:
            failures.append(f"cannot validate restart file: {exc}")

    log_text = args.model_log.read_text(errors="replace") if args.model_log.is_file() else ""
    for marker in (
        "HICAR discretely adjoint wind projection enabled",
        "HICAR SLEVE geometry gate:",
        "Simulation completed successfully!",
        "Timing across all compute images:",
    ):
        if marker not in log_text:
            failures.append(f"model log lacks completion marker: {marker}")

    provenance = validate_provenance(args, failures)
    payload = {
        "schema_version": 2,
        "status": "PASS" if not failures else "FAIL",
        "chunk_id": plan["chunk_id"],
        "start": plan["start"],
        "end": plan["end"],
        "hours": plan["hours"],
        "restart_continuation": args.restart_continuation,
        "output_profile": args.output_profile,
        "output_interval_seconds": args.output_interval_seconds,
        "static_file": str(args.static_file.resolve()) if args.static_file else None,
        "run_dir": str(args.run_dir.resolve()),
        "plan": str(args.plan.resolve()),
        "plan_sha256": sha256(args.plan),
        "output": {
            "files": output_artifacts,
            "size_bytes": sum(item["size_bytes"] for item in output_artifacts),
            "times": [value.isoformat() for value in output_times],
            "variables": output_variables,
            "ranges": output_ranges,
            "range_scope": (
                "static landmask; active USGS soil excludes water=16 and snow/ice=24"
                if args.output_profile == "qualification" and landmask is not None
                else (
                    "all cells at 10 m and 50/75/100/125/150/200 m AGL"
                    if args.output_profile == "wind_climatology"
                    else "all cells"
                )
            ),
        },
        "restart": {
            "path": str(args.restart_file.resolve()),
            "size_bytes": args.restart_file.stat().st_size if args.restart_file.is_file() else None,
            "sha256": sha256(args.restart_file) if args.restart_file.is_file() else None,
            "times": [value.isoformat() for value in restart_times],
            "dt_seconds": restart_dt_seconds,
            "variable_count": len(restart_variables),
        },
        "model_log": str(args.model_log.resolve()),
        "model_log_artifact": {
            "path": str(args.model_log.resolve()),
            "size_bytes": (
                args.model_log.stat().st_size if args.model_log.is_file() else None
            ),
            "sha256": sha256(args.model_log) if args.model_log.is_file() else None,
        },
        "provenance": provenance,
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print(
        f"PASS: model chunk {plan['chunk_id']} "
        f"output={sum(item['size_bytes'] for item in output_artifacts)} "
        f"restart={args.restart_file.stat().st_size} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
