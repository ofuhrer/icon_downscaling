#!/usr/bin/env python3
"""Screen retained month-pilot class means for persistent monotonic tendencies."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def linear_slope_per_day(times: list[datetime], values: list[float]) -> float:
    seconds = [(value - times[0]).total_seconds() for value in times]
    x_mean = sum(seconds) / len(seconds)
    y_mean = sum(values) / len(values)
    denominator = sum((value - x_mean) ** 2 for value in seconds)
    if not denominator:
        return 0.0
    slope = sum(
        (x - x_mean) * (y - y_mean) for x, y in zip(seconds, values)
    ) / denominator
    return slope * 86400.0


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month-plan", type=Path, required=True)
    parser.add_argument("--scientific-plan", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    for path, label in (
        (args.month_plan, "month plan"),
        (args.diagnostics, "month diagnostics"),
    ):
        if not path.is_file() or not Path(f"{path}.ready").is_file():
            raise SystemExit(f"{label} is not published: {path}")
    month = load_json(args.month_plan)
    scientific = load_json(args.scientific_plan)
    diagnostics = load_json(args.diagnostics)
    if month.get("status") != "PLANNED":
        raise SystemExit("month plan is not PLANNED")
    if diagnostics.get("status") != "PASS":
        raise SystemExit("month diagnostics is not PASS")

    criteria = scientific["promotion_criteria"]["month_to_annual_cycle"]
    screen = criteria["postspinup_drift_review"]
    retained_start = datetime.fromisoformat(month["start"]) + timedelta(
        days=int(criteria["declared_spinup_days"])
    )
    expected_records = int(criteria["expected_retained_output_records"])
    minimum_monotonic = float(
        screen["minimum_monotonic_increment_fraction_for_flag"]
    )
    minimum_range_ratio = float(
        screen["minimum_trend_span_to_retained_range_for_flag"]
    )
    absolute_thresholds = screen["minimum_absolute_trend_span_for_flag"]
    failures = []
    flags = []
    metrics = {}

    for class_name in screen["classes"]:
        class_payload = diagnostics.get("classes", {}).get(class_name)
        if not class_payload:
            failures.append(f"diagnostics lacks class {class_name}")
            continue
        series = class_payload["time_series"]
        times = [datetime.fromisoformat(value) for value in series["times"]]
        selected = [index for index, value in enumerate(times) if value >= retained_start]
        if len(selected) != expected_records:
            failures.append(
                f"{class_name} has {len(selected)} retained records; "
                f"expected {expected_records}"
            )
            continue
        retained_times = [times[index] for index in selected]
        metrics[class_name] = {}
        for variable in screen["variables"]:
            raw = series.get(variable)
            if raw is None or len(raw) != len(times):
                failures.append(f"{class_name} lacks complete {variable}")
                continue
            values = [float(raw[index]) for index in selected]
            if not all(math.isfinite(value) for value in values):
                failures.append(f"{class_name}/{variable} is non-finite")
                continue
            increments = [
                right - left for left, right in zip(values, values[1:])
            ]
            nonzero = [value for value in increments if value != 0.0]
            if nonzero:
                positive_fraction = sum(value > 0.0 for value in nonzero) / len(nonzero)
                negative_fraction = sum(value < 0.0 for value in nonzero) / len(nonzero)
                monotonic_fraction = max(positive_fraction, negative_fraction)
            else:
                positive_fraction = negative_fraction = monotonic_fraction = 0.0
            slope = linear_slope_per_day(retained_times, values)
            duration_days = (
                retained_times[-1] - retained_times[0]
            ).total_seconds() / 86400.0
            trend_span = slope * duration_days
            retained_range = max(values) - min(values)
            range_ratio = (
                abs(trend_span) / retained_range
                if retained_range > 0.0
                else (0.0 if trend_span == 0.0 else math.inf)
            )
            absolute_threshold = float(absolute_thresholds[variable])
            flagged = (
                monotonic_fraction >= minimum_monotonic
                and range_ratio >= minimum_range_ratio
                and abs(trend_span) >= absolute_threshold
            )
            identifier = f"{class_name}:{variable}"
            metrics[class_name][variable] = {
                "retained_record_count": len(values),
                "start": retained_times[0].isoformat(),
                "end": retained_times[-1].isoformat(),
                "minimum": min(values),
                "maximum": max(values),
                "linear_slope_per_day": slope,
                "linear_trend_span": trend_span,
                "retained_range": retained_range,
                "absolute_trend_span_to_range": range_ratio,
                "positive_increment_fraction": positive_fraction,
                "negative_increment_fraction": negative_fraction,
                "monotonic_increment_fraction": monotonic_fraction,
                "minimum_absolute_trend_span_for_flag": absolute_threshold,
                "flagged": flagged,
            }
            if flagged:
                flags.append(
                    {
                        "id": identifier,
                        "class": class_name,
                        "variable": variable,
                        "linear_slope_per_day": slope,
                        "linear_trend_span": trend_span,
                        "monotonic_increment_fraction": monotonic_fraction,
                        "absolute_trend_span_to_range": range_ratio,
                    }
                )

    payload = {
        "schema_version": 1,
        "status": "FAIL" if failures else "PASS",
        "decision": (
            "INCOMPLETE"
            if failures
            else ("ATTRIBUTION_REQUIRED" if flags else "NO_DRIFT_FLAGS")
        ),
        "month_plan": str(args.month_plan.resolve()),
        "diagnostics": str(args.diagnostics.resolve()),
        "retained_start": retained_start.isoformat(),
        "expected_retained_records": expected_records,
        "screen_contract": screen,
        "metrics": metrics,
        "flags": flags,
        "flag_count": len(flags),
        "interpretation": screen["interpretation"],
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    Path(f"{args.report}.ready").touch()
    print(f"PASS: month drift screen flags={len(flags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
