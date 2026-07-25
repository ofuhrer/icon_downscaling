#!/usr/bin/env python3
"""Compare a restart-segmented HICAR trajectory with an uninterrupted run."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np

from validate_model_chunk import QUALIFICATION_VARIABLES


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def canonical_time(value: datetime) -> datetime:
    result = datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
    )
    return result.replace(tzinfo=timezone.utc)


def decoded_times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    values = netCDF4.num2date(
        variable[:],
        variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )
    return [canonical_time(value) for value in values]


def published_completion(path: Path, label: str) -> dict:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} completion is not published: {path}")
    payload = load_json(path)
    if payload.get("status") != "PASS":
        raise ValueError(f"{label} completion is not PASS")
    if payload.get("output_profile") != "qualification":
        raise ValueError(f"{label} output profile is not qualification")
    return payload


def output_paths(completion: dict) -> list[Path]:
    return [Path(item["path"]) for item in completion["output"]["files"]]


def record_map(
    stack: ExitStack,
    paths: list[Path],
    label: str,
    failures: list[str],
) -> dict[datetime, tuple[netCDF4.Dataset, int]]:
    records = {}
    for path in paths:
        if not path.is_file():
            failures.append(f"{label} output is missing: {path}")
            continue
        dataset = stack.enter_context(netCDF4.Dataset(path))
        for index, valid in enumerate(decoded_times(dataset)):
            if valid in records:
                failures.append(f"{label} has duplicate time {valid.isoformat()}")
            records[valid] = (dataset, index)
    return records


def read_field(
    dataset: netCDF4.Dataset,
    name: str,
    index: int,
) -> np.ndarray:
    values = np.ma.asarray(dataset.variables[name][index])
    if np.ma.count_masked(values):
        values = values.filled(np.nan)
    return np.asarray(values, dtype=np.float64)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--segmented-completion",
        action="append",
        type=Path,
        required=True,
    )
    parser.add_argument("--reference-completion", type=Path, required=True)
    parser.add_argument("--scientific-plan", type=Path, required=True)
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    failures: list[str] = []
    plan = load_json(args.scientific_plan)
    criteria = plan["promotion_criteria"]["month_to_annual_cycle"]
    absolute_tolerances = criteria["restart_trajectory_absolute_tolerances"]
    variables = tuple(QUALIFICATION_VARIABLES)
    missing_tolerances = sorted(set(variables) - set(absolute_tolerances))
    unexpected_tolerances = sorted(set(absolute_tolerances) - set(variables))
    if missing_tolerances or unexpected_tolerances:
        raise SystemExit(
            "restart tolerance contract differs from qualification variables: "
            f"missing={missing_tolerances}, unexpected={unexpected_tolerances}"
        )
    relative_tolerance = float(criteria["restart_trajectory_relative_tolerance"])
    maximum_fraction = float(
        criteria["restart_trajectory_maximum_fraction_outside_tolerance"]
    )
    start = args.start.replace(tzinfo=timezone.utc)
    end = args.end.replace(tzinfo=timezone.utc)
    if end <= start:
        raise SystemExit("--end must be later than --start")

    segmented_completions = [
        published_completion(path, f"segmented {index}")
        for index, path in enumerate(args.segmented_completion, start=1)
    ]
    reference_completion = published_completion(
        args.reference_completion, "uninterrupted"
    )
    intervals = {
        int(completion["output_interval_seconds"])
        for completion in (*segmented_completions, reference_completion)
    }
    if len(intervals) != 1:
        raise SystemExit(f"output intervals differ: {sorted(intervals)}")
    interval_seconds = intervals.pop()
    interval = timedelta(seconds=interval_seconds)
    expected_times = []
    valid = start + interval
    while valid <= end:
        expected_times.append(valid)
        valid += interval
    minimum_hours = int(criteria["minimum_post_restart_overlap_hours"])
    if (end - start).total_seconds() < minimum_hours * 3600:
        raise SystemExit("comparison period is shorter than the frozen minimum")

    metrics = {}
    with ExitStack() as stack:
        segmented_records = record_map(
            stack,
            [
                path
                for completion in segmented_completions
                for path in output_paths(completion)
            ],
            "segmented",
            failures,
        )
        reference_records = record_map(
            stack,
            output_paths(reference_completion),
            "uninterrupted",
            failures,
        )
        segmented_coverage = [
            valid for valid in expected_times if valid in segmented_records
        ]
        reference_coverage = [
            valid for valid in expected_times if valid in reference_records
        ]
        if segmented_coverage != expected_times:
            failures.append("segmented output does not cover the comparison period")
        if reference_coverage != expected_times:
            failures.append("uninterrupted output does not cover the comparison period")

        for name in variables:
            count = 0
            missing_mismatch = 0
            outside = 0
            sum_squared = 0.0
            maximum_absolute = 0.0
            maximum_allowed = 0.0
            maximum_normalized = 0.0
            shape = None
            for valid in expected_times:
                if valid not in segmented_records or valid not in reference_records:
                    continue
                left_dataset, left_index = segmented_records[valid]
                right_dataset, right_index = reference_records[valid]
                if (
                    name not in left_dataset.variables
                    or name not in right_dataset.variables
                ):
                    failures.append(f"{name} is missing at {valid.isoformat()}")
                    continue
                left = read_field(left_dataset, name, left_index)
                right = read_field(right_dataset, name, right_index)
                if left.shape != right.shape:
                    failures.append(
                        f"{name} shape differs at {valid.isoformat()}: "
                        f"{left.shape} != {right.shape}"
                    )
                    continue
                shape = left.shape
                finite_left = np.isfinite(left)
                finite_right = np.isfinite(right)
                missing_mismatch += int(np.count_nonzero(finite_left != finite_right))
                finite = finite_left & finite_right
                if not np.any(finite):
                    continue
                difference = np.abs(left[finite] - right[finite])
                allowed = float(absolute_tolerances[name]) + (
                    relative_tolerance * np.abs(right[finite])
                )
                count += int(finite.sum())
                outside += int(np.count_nonzero(difference > allowed))
                sum_squared += float(np.sum(difference * difference))
                maximum_absolute = max(maximum_absolute, float(np.max(difference)))
                maximum_allowed = max(maximum_allowed, float(np.max(allowed)))
                maximum_normalized = max(
                    maximum_normalized,
                    float(np.max(difference / allowed)),
                )
            fraction_outside = outside / count if count else None
            metrics[name] = {
                "shape_per_record": list(shape) if shape is not None else None,
                "joint_finite_count": count,
                "missing_mismatch_count": missing_mismatch,
                "outside_tolerance_count": outside,
                "fraction_outside_tolerance": fraction_outside,
                "root_mean_squared_error": (
                    math.sqrt(sum_squared / count) if count else None
                ),
                "maximum_absolute_error": (maximum_absolute if count else None),
                "maximum_allowed_at_sample": (maximum_allowed if count else None),
                "maximum_normalized_error": (maximum_normalized if count else None),
                "absolute_tolerance": float(absolute_tolerances[name]),
                "relative_tolerance": relative_tolerance,
            }
            if not count:
                failures.append(f"{name} has no jointly finite comparison data")
            if missing_mismatch:
                failures.append(
                    f"{name} has {missing_mismatch} missing-value mismatches"
                )
            if fraction_outside is None or fraction_outside > maximum_fraction:
                failures.append(
                    f"{name} fraction outside tolerance is {fraction_outside}; "
                    f"maximum is {maximum_fraction}"
                )

    payload = {
        "schema_version": 1,
        "status": "FAIL" if failures else "PASS",
        "interpretation": (
            "The segmented trajectory and uninterrupted reference start from "
            "the same exact-end restart. Every three-hour qualification field "
            "is compared for the frozen post-boundary period; zero samples may "
            "exceed the predeclared absolute-plus-relative tolerance."
        ),
        "start_exclusive": start.isoformat(),
        "end_inclusive": end.isoformat(),
        "expected_times": [value.isoformat() for value in expected_times],
        "output_interval_seconds": interval_seconds,
        "segmented_completions": [
            str(path.resolve()) for path in args.segmented_completion
        ],
        "reference_completion": str(args.reference_completion.resolve()),
        "scientific_plan": str(args.scientific_plan.resolve()),
        "maximum_fraction_outside_tolerance": maximum_fraction,
        "metrics": metrics,
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print(f"PASS: restart trajectory matches over {len(expected_times)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
