#!/usr/bin/env python3
"""Compare sparse HICAR columns with source forcing at matching valid times.

The comparison deliberately uses source-model heights rather than level
numbers.  HICAR and ICON use different terrain and vertical coordinates, so a
level-by-level comparison would mix genuine atmospheric differences with a
coordinate mismatch.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


CASE = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = CASE / "config" / "domain.json"
EARTH_RADIUS_M = 6_371_000.0

VARIABLES = {
    "u": {
        "forcing": "U",
        "units": "m s-1",
        "min_correlation": 0.30,
        "max_rmse": 18.0,
        "max_abs_bias": 8.0,
    },
    "v": {
        "forcing": "V",
        "units": "m s-1",
        "min_correlation": 0.30,
        "max_rmse": 18.0,
        "max_abs_bias": 8.0,
    },
    "w": {
        "forcing": "W",
        "units": "m s-1",
        "max_rmse": 8.0,
        "max_p95_abs": 8.0,
    },
    "temperature": {
        "forcing": "T",
        "units": "K",
        "min_correlation": 0.90,
        "max_rmse": 12.0,
        "max_abs_bias": 5.0,
    },
    "pressure": {
        "forcing": "P",
        "units": "Pa",
        "min_correlation": 0.95,
        "max_normalized_mae": 0.12,
    },
    "qv": {
        "forcing": "QV",
        "units": "kg kg-1",
        "min_correlation": 0.50,
        "max_mae": 0.004,
        "max_abs_bias": 0.002,
    },
}


def nearest_axis_indices(axis: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Return indices of the nearest values on a monotonic one-dimensional axis."""
    axis = np.asarray(axis, dtype=np.float64)
    if axis.ndim != 1 or axis.size < 2:
        raise ValueError("source coordinate axis must be one-dimensional")
    increasing = axis[-1] > axis[0]
    ordered = axis if increasing else axis[::-1]
    if np.any(np.diff(ordered) <= 0):
        raise ValueError("source coordinate axis is not strictly monotonic")
    right = np.searchsorted(ordered, values, side="left")
    right = np.clip(right, 1, ordered.size - 1)
    left = right - 1
    choose_right = np.abs(ordered[right] - values) < np.abs(ordered[left] - values)
    selected = np.where(choose_right, right, left)
    return selected if increasing else axis.size - 1 - selected


def interpolate_columns(
    source_height: np.ndarray,
    source_value: np.ndarray,
    target_height: np.ndarray,
) -> np.ndarray:
    """Interpolate independent source columns to target geometric heights."""
    if source_height.shape != source_value.shape:
        raise ValueError("source height/value shapes differ")
    if target_height.ndim != 2 or target_height.shape[1] != source_height.shape[1]:
        raise ValueError("target and source column counts differ")
    result = np.full(target_height.shape, np.nan, dtype=np.float64)
    for column in range(source_height.shape[1]):
        height = np.asarray(source_height[:, column], dtype=np.float64)
        value = np.asarray(source_value[:, column], dtype=np.float64)
        valid = np.isfinite(height) & np.isfinite(value)
        if np.count_nonzero(valid) < 2:
            continue
        order = np.argsort(height[valid])
        height = height[valid][order]
        value = value[valid][order]
        unique = np.concatenate(([True], np.diff(height) > 0.0))
        height = height[unique]
        value = value[unique]
        if height.size < 2:
            continue
        target = target_height[:, column]
        inside = np.isfinite(target) & (target >= height[0]) & (target <= height[-1])
        result[inside, column] = np.interp(target[inside], height, value)
    return result


def metric_summary(hicar: np.ndarray, source: np.ndarray) -> dict[str, float | int]:
    valid = np.isfinite(hicar) & np.isfinite(source)
    if not np.any(valid):
        raise ValueError("comparison has no finite paired samples")
    model = np.asarray(hicar[valid], dtype=np.float64)
    driving = np.asarray(source[valid], dtype=np.float64)
    difference = model - driving
    abs_difference = np.abs(difference)
    source_scale = float(np.mean(np.abs(driving)))
    if model.size > 1 and np.std(model) > 0.0 and np.std(driving) > 0.0:
        correlation = float(np.corrcoef(model, driving)[0, 1])
    else:
        correlation = math.nan
    return {
        "paired_samples": int(model.size),
        "bias": float(np.mean(difference)),
        "mae": float(np.mean(abs_difference)),
        "rmse": float(np.sqrt(np.mean(difference * difference))),
        "p95_abs": float(np.quantile(abs_difference, 0.95)),
        "correlation": correlation,
        "normalized_mae": float(np.mean(abs_difference) / max(source_scale, 1.0e-12)),
        "hicar_min": float(np.min(model)),
        "hicar_max": float(np.max(model)),
        "source_min": float(np.min(driving)),
        "source_max": float(np.max(driving)),
    }


def decoded_time(dataset: netCDF4.Dataset, index: int) -> str:
    variable = dataset.variables["time"]
    value = netCDF4.num2date(
        variable[index],
        units=variable.units,
        calendar=getattr(variable, "calendar", "standard"),
        only_use_cftime_datetimes=True,
    )
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def read_hicar_sample(
    dataset: netCDF4.Dataset,
    time_index: int,
    y_slice: slice,
    x_slice: slice,
    mask: np.ndarray,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    height = np.asarray(dataset.variables["z"][:, y_slice, x_slice], dtype=np.float64)
    fields: dict[str, np.ndarray] = {
        "temperature": np.asarray(
            dataset.variables["temperature"][time_index, :, y_slice, x_slice],
            dtype=np.float64,
        ),
        "pressure": np.asarray(
            dataset.variables["pressure"][time_index, :, y_slice, x_slice],
            dtype=np.float64,
        ),
        "qv": np.asarray(
            dataset.variables["qv"][time_index, :, y_slice, x_slice],
            dtype=np.float64,
        ),
        "w": np.asarray(
            dataset.variables["w"][time_index, :, y_slice, x_slice],
            dtype=np.float64,
        ),
    }
    # HICAR writes horizontal winds on Arakawa-C faces.  Average the two
    # adjacent faces to compare with cell-centred ICON forcing.
    x_left = x_slice
    x_right = slice(x_slice.start + 1, x_slice.stop + 1, x_slice.step)
    y_low = y_slice
    y_high = slice(y_slice.start + 1, y_slice.stop + 1, y_slice.step)
    fields["u"] = 0.5 * (
        np.asarray(dataset.variables["u"][time_index, :, y_slice, x_left])
        + np.asarray(dataset.variables["u"][time_index, :, y_slice, x_right])
    )
    fields["v"] = 0.5 * (
        np.asarray(dataset.variables["v"][time_index, :, y_low, x_slice])
        + np.asarray(dataset.variables["v"][time_index, :, y_high, x_slice])
    )
    return (
        {name: values[:, mask] for name, values in fields.items()},
        height[:, mask],
    )


def forcing_columns(
    dataset: netCDF4.Dataset,
    name: str,
    y_indices: np.ndarray,
    x_indices: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if name == "W":
        heights = np.asarray(dataset.variables["HHL"][:, :, :], dtype=np.float64)
    else:
        heights = np.asarray(dataset.variables["HFL"][:, :, :], dtype=np.float64)
    values = np.asarray(dataset.variables[name][0, :, :, :], dtype=np.float64)
    return heights[:, y_indices, x_indices], values[:, y_indices, x_indices]


def apply_gates(name: str, metrics: dict[str, float | int]) -> list[str]:
    specification = VARIABLES[name]
    failures: list[str] = []
    if metrics["paired_samples"] < 10_000:
        failures.append(f"{name}: only {metrics['paired_samples']} paired samples")
    checks = (
        ("min_correlation", "correlation", lambda value, limit: value >= limit),
        ("max_rmse", "rmse", lambda value, limit: value <= limit),
        ("max_abs_bias", "bias", lambda value, limit: abs(value) <= limit),
        ("max_p95_abs", "p95_abs", lambda value, limit: value <= limit),
        ("max_mae", "mae", lambda value, limit: value <= limit),
        ("max_normalized_mae", "normalized_mae", lambda value, limit: value <= limit),
    )
    for gate_name, metric_name, predicate in checks:
        if gate_name not in specification:
            continue
        value = float(metrics[metric_name])
        limit = float(specification[gate_name])
        if not math.isfinite(value) or not predicate(value, limit):
            failures.append(f"{name}: {metric_name}={value:.6g} fails {gate_name}={limit:.6g}")
    return failures


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hicar_output", type=Path)
    parser.add_argument(
        "--forcing",
        type=Path,
        action="append",
        required=True,
        help="Forcing file corresponding to each HICAR time record; repeat in order.",
    )
    parser.add_argument(
        "--hicar-time-index",
        type=int,
        action="append",
        help=(
            "HICAR time index corresponding to each --forcing argument. "
            "By default all HICAR records are compared in order."
        ),
    )
    parser.add_argument("--domain-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--horizontal-stride", type=int, default=40)
    parser.add_argument("--bbox-padding-degrees", type=float, default=0.02)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    plan = json.loads(args.domain_plan.read_text())
    bbox = plan["switzerland_bbox"]
    if args.horizontal_stride < 1:
        raise SystemExit("--horizontal-stride must be positive")

    failures: list[str] = []
    endpoint_reports: list[dict] = []
    with netCDF4.Dataset(args.hicar_output) as hicar:
        required_hicar = {"time", "lat", "lon", "z", *VARIABLES}
        missing = sorted(required_hicar - set(hicar.variables))
        if missing:
            raise SystemExit(f"HICAR output lacks variables: {', '.join(missing)}")
        time_indices = (
            args.hicar_time_index
            if args.hicar_time_index is not None
            else list(range(len(hicar.dimensions["time"])))
        )
        if len(args.forcing) != len(time_indices):
            raise SystemExit(
                f"{len(args.forcing)} forcing files supplied for "
                f"{len(time_indices)} selected HICAR records"
            )
        if any(index < 0 or index >= len(hicar.dimensions["time"]) for index in time_indices):
            raise SystemExit("--hicar-time-index is outside the HICAR time dimension")

        ny, nx = hicar.variables["lat"].shape
        y_slice = slice(0, ny, args.horizontal_stride)
        x_slice = slice(0, nx, args.horizontal_stride)
        sample_lat = np.asarray(hicar.variables["lat"][y_slice, x_slice], dtype=np.float64)
        sample_lon = np.asarray(hicar.variables["lon"][y_slice, x_slice], dtype=np.float64)
        padding = args.bbox_padding_degrees
        inside = (
            (sample_lon >= bbox["west_lon"] + padding)
            & (sample_lon <= bbox["east_lon"] - padding)
            & (sample_lat >= bbox["south_lat"] + padding)
            & (sample_lat <= bbox["north_lat"] - padding)
        )
        if np.count_nonzero(inside) < 100:
            raise SystemExit("too few sampled HICAR columns fall inside the Swiss comparison box")
        point_lat = sample_lat[inside]
        point_lon = sample_lon[inside]

        for time_index, forcing_path in zip(time_indices, args.forcing, strict=True):
            with netCDF4.Dataset(forcing_path) as forcing:
                required_forcing = {"time", "lat_1", "lon_1", "HFL", "HHL"}
                required_forcing.update(specification["forcing"] for specification in VARIABLES.values())
                missing = sorted(required_forcing - set(forcing.variables))
                if missing:
                    raise SystemExit(f"{forcing_path} lacks variables: {', '.join(missing)}")
                hicar_time = decoded_time(hicar, time_index)
                forcing_time = decoded_time(forcing, 0)
                if hicar_time != forcing_time:
                    failures.append(
                        f"time record {time_index}: HICAR {hicar_time} != forcing {forcing_time}"
                    )

                source_lat_2d = np.asarray(forcing.variables["lat_1"][:, :], dtype=np.float64)
                source_lon_2d = np.asarray(forcing.variables["lon_1"][:, :], dtype=np.float64)
                lat_axis = source_lat_2d[:, 0]
                lon_axis = source_lon_2d[0, :]
                regularity = {
                    "max_lat_column_deviation_degrees": float(
                        np.max(np.abs(source_lat_2d - lat_axis[:, None]))
                    ),
                    "max_lon_row_deviation_degrees": float(
                        np.max(np.abs(source_lon_2d - lon_axis[None, :]))
                    ),
                }
                if max(regularity.values()) > 1.0e-6:
                    raise SystemExit("forcing latitude/longitude grid is not separable and regular")
                source_y = nearest_axis_indices(lat_axis, point_lat)
                source_x = nearest_axis_indices(lon_axis, point_lon)
                matched_lat = lat_axis[source_y]
                matched_lon = lon_axis[source_x]
                dlat = np.radians(matched_lat - point_lat)
                dlon = np.radians(matched_lon - point_lon)
                mean_lat = np.radians(0.5 * (matched_lat + point_lat))
                distance = EARTH_RADIUS_M * np.sqrt(dlat * dlat + (np.cos(mean_lat) * dlon) ** 2)
                distance_summary = {
                    "maximum_m": float(np.max(distance)),
                    "p95_m": float(np.quantile(distance, 0.95)),
                    "mean_m": float(np.mean(distance)),
                }
                if distance_summary["maximum_m"] > 900.0:
                    failures.append(
                        f"time record {time_index}: nearest forcing point is "
                        f"{distance_summary['maximum_m']:.1f} m away"
                    )

                hicar_fields, hicar_height = read_hicar_sample(
                    hicar, time_index, y_slice, x_slice, inside
                )
                metrics: dict[str, dict] = {}
                for hicar_name, specification in VARIABLES.items():
                    source_height, source_value = forcing_columns(
                        forcing, specification["forcing"], source_y, source_x
                    )
                    source_on_hicar = interpolate_columns(
                        source_height, source_value, hicar_height
                    )
                    summary = metric_summary(hicar_fields[hicar_name], source_on_hicar)
                    summary["units"] = specification["units"]
                    summary["gates"] = {
                        key: value
                        for key, value in specification.items()
                        if key.startswith("min_") or key.startswith("max_")
                    }
                    metrics[hicar_name] = summary
                    failures.extend(
                        f"time record {time_index}: {failure}"
                        for failure in apply_gates(hicar_name, summary)
                    )

                endpoint_reports.append(
                    {
                        "hicar_time_index": time_index,
                        "valid_time": hicar_time,
                        "forcing_file": str(forcing_path.resolve()),
                        "sampled_hicar_columns": int(np.count_nonzero(inside)),
                        "nearest_source_distance": distance_summary,
                        "forcing_grid_regularity": regularity,
                        "metrics": metrics,
                    }
                )

    payload = {
        "status": "PASS" if not failures else "FAIL",
        "hicar_output": str(args.hicar_output.resolve()),
        "domain_plan": str(args.domain_plan.resolve()),
        "method": {
            "horizontal": (
                "HICAR columns sampled on a regular index stride inside the Swiss bbox; "
                "nearest point on the regular 0.01-degree forcing grid"
            ),
            "vertical": (
                "independent linear interpolation of forcing columns from HFL (HHL for W) "
                "to HICAR geometric mass-level height; no extrapolation"
            ),
            "horizontal_wind": "adjacent HICAR Arakawa-C faces averaged to mass points",
            "horizontal_stride": args.horizontal_stride,
            "bbox_padding_degrees": args.bbox_padding_degrees,
        },
        "endpoints": endpoint_reports,
        "failures": failures,
    }
    write_json_atomic(args.report, payload)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        f"PASS: source-aware comparison of {len(endpoint_reports)} HICAR records; "
        f"report={args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
