#!/usr/bin/env python3
"""Compare two REA-L full-column GRIB bundles at the same valid time."""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from eccodes import (
    codes_get,
    codes_get_values,
    codes_grib_new_from_file,
    codes_release,
)


def empty_accumulator() -> dict[str, float | int]:
    return {
        "fields": 0,
        "points": 0,
        "sum_a": 0.0,
        "sum_b": 0.0,
        "sum_a2": 0.0,
        "sum_b2": 0.0,
        "sum_ab": 0.0,
        "sum_difference": 0.0,
        "sum_squared_difference": 0.0,
        "maximum_absolute_difference": 0.0,
    }


def finalize(accumulator: dict[str, float | int]) -> dict[str, float | int]:
    count = int(accumulator["points"])
    mean_a = float(accumulator["sum_a"]) / count
    mean_b = float(accumulator["sum_b"]) / count
    variance_a = float(accumulator["sum_a2"]) / count - mean_a * mean_a
    variance_b = float(accumulator["sum_b2"]) / count - mean_b * mean_b
    covariance = float(accumulator["sum_ab"]) / count - mean_a * mean_b
    denominator = math.sqrt(max(variance_a, 0.0) * max(variance_b, 0.0))
    return {
        "fields": int(accumulator["fields"]),
        "points": count,
        "bias_b_minus_a": float(accumulator["sum_difference"]) / count,
        "rmse": math.sqrt(float(accumulator["sum_squared_difference"]) / count),
        "maximum_absolute_difference": float(accumulator["maximum_absolute_difference"]),
        "correlation": covariance / denominator if denominator > 0.0 else None,
    }


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
    parser.add_argument("cycle_step24", type=Path)
    parser.add_argument("next_cycle_step0", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    accumulators: dict[str, dict[str, float | int]] = defaultdict(empty_accumulator)
    valid_times_a: set[str] = set()
    valid_times_b: set[str] = set()
    messages = 0
    with args.cycle_step24.open("rb") as stream_a, args.next_cycle_step0.open("rb") as stream_b:
        while True:
            handle_a = codes_grib_new_from_file(stream_a)
            handle_b = codes_grib_new_from_file(stream_b)
            if handle_a is None or handle_b is None:
                if handle_a is not None:
                    codes_release(handle_a)
                if handle_b is not None:
                    codes_release(handle_b)
                if handle_a is not None or handle_b is not None:
                    raise SystemExit("GRIB bundles have different message counts")
                break
            try:
                key_a = (
                    str(codes_get(handle_a, "paramId")),
                    str(codes_get(handle_a, "typeOfLevel")),
                    int(codes_get(handle_a, "level")),
                )
                key_b = (
                    str(codes_get(handle_b, "paramId")),
                    str(codes_get(handle_b, "typeOfLevel")),
                    int(codes_get(handle_b, "level")),
                )
                if key_a != key_b:
                    raise SystemExit(f"message ordering differs: {key_a} != {key_b}")
                valid_times_a.add(
                    f"{int(codes_get(handle_a, 'validityDate')):08d}"
                    f"T{int(codes_get(handle_a, 'validityTime')):04d}"
                )
                valid_times_b.add(
                    f"{int(codes_get(handle_b, 'validityDate')):08d}"
                    f"T{int(codes_get(handle_b, 'validityTime')):04d}"
                )
                values_a = np.asarray(codes_get_values(handle_a), dtype=np.float64)
                values_b = np.asarray(codes_get_values(handle_b), dtype=np.float64)
                if values_a.shape != values_b.shape:
                    raise SystemExit(f"field shapes differ for {key_a}")
                if not np.isfinite(values_a).all() or not np.isfinite(values_b).all():
                    raise SystemExit(f"nonfinite values found for {key_a}")
                difference = values_b - values_a
                name = str(codes_get(handle_a, "shortName"))
                accumulator = accumulators[name]
                accumulator["fields"] = int(accumulator["fields"]) + 1
                accumulator["points"] = int(accumulator["points"]) + values_a.size
                accumulator["sum_a"] = float(accumulator["sum_a"]) + float(np.sum(values_a))
                accumulator["sum_b"] = float(accumulator["sum_b"]) + float(np.sum(values_b))
                accumulator["sum_a2"] = float(accumulator["sum_a2"]) + float(
                    np.dot(values_a, values_a)
                )
                accumulator["sum_b2"] = float(accumulator["sum_b2"]) + float(
                    np.dot(values_b, values_b)
                )
                accumulator["sum_ab"] = float(accumulator["sum_ab"]) + float(
                    np.dot(values_a, values_b)
                )
                accumulator["sum_difference"] = float(
                    accumulator["sum_difference"]
                ) + float(np.sum(difference))
                accumulator["sum_squared_difference"] = float(
                    accumulator["sum_squared_difference"]
                ) + float(np.dot(difference, difference))
                accumulator["maximum_absolute_difference"] = max(
                    float(accumulator["maximum_absolute_difference"]),
                    float(np.max(np.abs(difference))),
                )
                messages += 1
            finally:
                codes_release(handle_a)
                codes_release(handle_b)

    if len(valid_times_a) != 1 or valid_times_a != valid_times_b:
        raise SystemExit(
            f"bundles do not have one matching valid time: {valid_times_a} vs {valid_times_b}"
        )
    payload = {
        "status": "PASS",
        "valid_time": next(iter(valid_times_a)),
        "cycle_step24": str(args.cycle_step24.resolve()),
        "next_cycle_step0": str(args.next_cycle_step0.resolve()),
        "messages": messages,
        "variables": {
            name: finalize(accumulator)
            for name, accumulator in sorted(accumulators.items())
        },
        "identical": all(
            float(accumulator["maximum_absolute_difference"]) == 0.0
            for accumulator in accumulators.values()
        ),
    }
    write_json_atomic(args.report, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
