#!/usr/bin/env python3
"""Assess bounded coupled HICAR runs without selecting a soil-water hypothesis."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import netCDF4
import numpy as np


METHODS = ("smi", "relative_saturation")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sha_records(path: Path) -> list[tuple[str, Path]]:
    if not path.is_file():
        raise ValueError(f"missing checksum record: {path}")
    records: list[tuple[str, Path]] = []
    for line in path.read_text().splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
            raise ValueError(f"malformed checksum record: {path}")
        target = Path(fields[1].lstrip("*"))
        if not target.is_file() or sha256(target) != fields[0]:
            raise ValueError(f"checksum target is missing or changed: {target}")
        records.append((fields[0], target))
    if not records:
        raise ValueError(f"empty checksum record: {path}")
    return records


def read_sha_record(path: Path) -> tuple[str, Path]:
    records = read_sha_records(path)
    if len(records) != 1:
        raise ValueError(f"expected one checksum target in {path}")
    return records[0]


def quantiles(values: np.ndarray) -> dict[str, float]:
    array = np.ma.asarray(values, dtype=np.float64).compressed()
    array = array[np.isfinite(array)]
    if array.size == 0:
        raise ValueError("diagnostic field has no finite, unmasked values")
    result = np.quantile(array, (0.0, 0.01, 0.5, 0.99, 1.0))
    return dict(zip(("minimum", "p01", "p50", "p99", "maximum"), map(float, result)))


def parse_run(value: str) -> tuple[str, str, Path]:
    try:
        case_date, method, path = value.split("=", 2)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("run must be DATE=METHOD=PATH") from exc
    if len(case_date) != 8 or not case_date.isdigit():
        raise argparse.ArgumentTypeError("run date must be YYYYMMDD")
    if method not in METHODS:
        raise argparse.ArgumentTypeError(f"method must be one of {METHODS}")
    return case_date, method, Path(path)


def read_run(case_date: str, method: str, run: Path) -> tuple[dict, dict[str, np.ndarray]]:
    log = run / "model.out"
    if not log.is_file() or not Path(f"{log}.ready").is_file():
        raise ValueError(f"unpublished coupled run: {run}")
    log_text = log.read_text(errors="replace")
    for marker in (
        "Reading Land Variables",
        "Read surface temperature field from: surface_temperature",
        "Simulation completed successfully!",
    ):
        if marker not in log_text:
            raise ValueError(f"{run}: model log lacks {marker!r}")
    commit_match = re.search(r"Git commit:\s*([0-9a-f]+)", log_text)
    if commit_match is None:
        raise ValueError(f"{run}: model log lacks HICAR source identity")
    executable_sha256, executable = read_sha_record(run / "executable.sha256")
    runtime_sha256, runtime_domain = read_sha_record(run / "runtime_domain.sha256")
    forcing_records = read_sha_records(run / "forcing.sha256")
    if len(forcing_records) != 2:
        raise ValueError(f"{run}: ten-minute smoke requires exactly two hourly forcing records")
    namelist = run / "input.nml"
    if not namelist.is_file():
        raise ValueError(f"{run}: missing input.nml")
    namelist_text = namelist.read_text()
    runtime_match = re.search(
        r"^\s*init_conditions_file\s*=\s*'([^']+)'", namelist_text, re.MULTILINE
    )
    if runtime_match is None or Path(runtime_match.group(1)).resolve() != runtime_domain.resolve():
        raise ValueError(f"{run}: input.nml and runtime checksum identify different domains")
    query = run / "soiltexture_var_query.txt"
    if (
        not query.is_file()
        or "Namelist Variable:" not in query.read_text(errors="replace")
        or "not a valid namelist variable" in query.read_text(errors="replace")
    ):
        raise ValueError(f"{run}: depth-varying soil-texture preflight did not pass")
    provenance = executable.parent / "hicar_build_provenance.txt"
    if not provenance.is_file():
        raise ValueError(f"{run}: executable build provenance is missing")
    provenance_values = dict(
        line.split("=", 1)
        for line in provenance.read_text().splitlines()
        if "=" in line and not line.startswith((" ", "\t"))
    )
    source_commit = provenance_values.get("source_commit", "")
    if not source_commit.startswith(commit_match.group(1)):
        raise ValueError(f"{run}: log and build provenance identify different HICAR commits")
    if provenance_values.get("variant") != "gpu-nccl":
        raise ValueError(f"{run}: coupled qualification did not use the GPU-NCCL build")
    with netCDF4.Dataset(runtime_domain) as runtime:
        if str(getattr(runtime, "land_state_soil_water_method", "")) != method:
            raise ValueError(f"{run}: runtime domain carries the wrong soil-water method")
        expected_valid_time = (
            f"{case_date[:4]}-{case_date[4:6]}-{case_date[6:]}T00:00:00Z"
        )
        if str(getattr(runtime, "land_state_valid_time", "")) != expected_valid_time:
            raise ValueError(f"{run}: runtime domain carries the wrong valid time")
        if "soil_type_layer" not in runtime.variables or runtime["soil_type_layer"].shape[0] != 4:
            raise ValueError(f"{run}: runtime domain lacks four-layer soil texture")
        land = np.asarray(runtime["landmask"][:], dtype=np.float64) >= 0.5
        landuse = np.asarray(runtime["landuse"][:], dtype=np.int64)
        active_soil = land & (landuse != 24)
        if not np.any(active_soil):
            raise ValueError(f"{run}: runtime domain has no active non-glacier soil")
        epoch_policy = str(
            getattr(runtime, "land_state_static_epoch_back_extrapolation", "unknown")
        )
    outputs = sorted((run / "output").glob("*.nc"))
    output_records: list[tuple[object, Path, int]] = []
    for candidate in outputs:
        with netCDF4.Dataset(candidate) as dataset:
            if "time" not in dataset.variables:
                raise ValueError(f"{candidate}: output lacks time coordinate")
            variable = dataset["time"]
            decoded = netCDF4.num2date(
                variable[:],
                variable.units,
                calendar=getattr(variable, "calendar", "standard"),
            ).ravel()
            output_records.extend(
                (timestamp, candidate, index) for index, timestamp in enumerate(decoded)
            )
    if len(output_records) < 2:
        raise ValueError(f"{run}: no evolved output after the initial state")
    output_records.sort(key=lambda record: record[0])
    initial_timestamp = output_records[0][0]
    final_timestamp, output, output_time_index = output_records[-1]
    elapsed_seconds = (final_timestamp - initial_timestamp).total_seconds()
    if not 599.0 <= elapsed_seconds <= 720.0:
        raise ValueError(f"{run}: evolved output span is {elapsed_seconds} s instead of 10 minutes")
    limits = {
        "soil_water_content": (0.0, 1.0),
        "soil_temperature": (180.0, 340.0),
        "tsfe": (180.0, 350.0),
        "snow_height": (0.0, 20.0),
        "soil_column_total_water": (0.0, 10_000.0),
        "hfss": (-5_000.0, 5_000.0),
        "hfls": (-5_000.0, 5_000.0),
    }
    diagnostics: dict[str, dict[str, float]] = {}
    diagnostic_support: dict[str, str] = {}
    arrays: dict[str, np.ndarray] = {}
    warnings: list[str] = []
    with netCDF4.Dataset(output) as dataset:
        missing = sorted(set(limits) - set(dataset.variables))
        if missing:
            raise ValueError(f"{output}: missing requested coupled diagnostics {missing}")
        for name, (lower, upper) in limits.items():
            variable = dataset[name]
            values = np.ma.asarray(variable[:], dtype=np.float64)
            if "time" in variable.dimensions:
                values = np.take(
                    values, output_time_index, axis=variable.dimensions.index("time")
                )
            support = (
                active_soil
                if name in {"soil_water_content", "soil_column_total_water"}
                else land
            )
            values = np.ma.masked_where(
                ~np.broadcast_to(support, values.shape), values
            )
            finite = values.compressed()
            if finite.size == 0 or not np.isfinite(finite).all():
                raise ValueError(f"{output}: {name} has no valid finite support")
            if np.any((finite < lower) | (finite > upper)):
                raise ValueError(f"{output}: {name} lies outside {lower}..{upper}")
            diagnostics[name] = quantiles(values)
            diagnostics[name]["absolute_p99"] = float(
                np.quantile(np.abs(values.compressed()), 0.99)
            )
            arrays[name] = np.asarray(values.filled(np.nan), dtype=np.float64)
            diagnostic_support[name] = (
                "active_non_glacier_soil" if support is active_soil else "land"
            )
            if name in {"hfss", "hfls"} and np.max(np.abs(values.compressed())) > 500.0:
                warnings.append(
                    f"{name}: isolated active-land magnitude exceeds 500 W m-2; "
                    "inspect startup relaxation in a longer response experiment"
                )
    payload = {
        "case_date": case_date,
        "method": method,
        "run": str(run.resolve()),
        "output": str(output.resolve()),
        "initial_output_time": initial_timestamp.isoformat(),
        "evolved_output_time": final_timestamp.isoformat(),
        "evolved_output_elapsed_seconds": elapsed_seconds,
        "diagnostics": diagnostics,
        "diagnostic_support": diagnostic_support,
        "warnings": warnings,
        "provenance": {
            "hicar_source_commit": source_commit,
            "executable": str(executable.resolve()),
            "executable_sha256": executable_sha256,
            "runtime_domain": str(runtime_domain.resolve()),
            "runtime_domain_sha256": runtime_sha256,
            "forcing": [
                {"path": str(path.resolve()), "sha256": digest}
                for digest, path in forcing_records
            ],
            "static_epoch_back_extrapolation": epoch_policy,
        },
    }
    return payload, arrays


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=parse_run, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = {(date, method): path for date, method, path in args.run}
    dates = sorted({date for date, _ in runs})
    expected = {(date, method) for date in dates for method in METHODS}
    if set(runs) != expected or len(dates) < 2:
        raise SystemExit("provide both SMI and relative-saturation runs for at least two dates")

    payloads: dict[str, dict] = {}
    arrays: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    comparisons: dict[str, dict] = {}
    for key, path in sorted(runs.items()):
        payload, data = read_run(*key, path)
        payloads[f"{key[0]}_{key[1]}"] = payload
        arrays[key] = data
    for date in dates:
        left_forcing = payloads[f"{date}_smi"]["provenance"]["forcing"]
        right_forcing = payloads[f"{date}_relative_saturation"]["provenance"]["forcing"]
        if left_forcing != right_forcing:
            raise ValueError(f"{date}: SMI and relative-saturation arms use different forcing")
        left = arrays[(date, "smi")]["soil_water_content"]
        right = arrays[(date, "relative_saturation")]["soil_water_content"]
        if left.shape != right.shape:
            raise ValueError(f"{date}: coupled soil-water output shapes differ")
        difference = left - right
        finite = np.isfinite(difference)
        if not np.any(finite):
            raise ValueError(f"{date}: coupled A/B difference has no common support")
        if float(np.max(np.abs(difference[finite]))) <= 1.0e-6:
            raise ValueError(f"{date}: SMI and relative-saturation arms are not distinct")
        comparisons[date] = {
            "smi_minus_relative_saturation_soil_water_content": quantiles(
                np.ma.masked_invalid(difference)
            )
        }
    executable_hashes = {
        payload["provenance"]["executable_sha256"] for payload in payloads.values()
    }
    source_commits = {
        payload["provenance"]["hicar_source_commit"] for payload in payloads.values()
    }
    if len(executable_hashes) != 1 or len(source_commits) != 1:
        raise ValueError("coupled smoke arms do not use one executable/source identity")
    report = {
        "status": "PASS_COUPLED_SMOKE_PLAUSIBILITY",
        "qualification": "TEN_MINUTE_EVOLVED_STATE_AND_NUMERICAL_PLAUSIBILITY_ONLY",
        "policy_decision": "NOT_DETERMINED_BY_SHORT_SMOKE_TESTS",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "runs": payloads,
        "comparisons": comparisons,
        "warnings": [
            f"{name}: {warning}"
            for name, payload in payloads.items()
            for warning in payload["warnings"]
        ],
    }
    write_atomic(args.output, report)
    Path(f"{args.output}.ready").touch()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
