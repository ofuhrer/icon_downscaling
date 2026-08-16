"""Validation and indexing of target-native HICAR lateral-boundary snapshots."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np

from .pipeline import manifest_identity
from .products import sha256


EXPECTED_LATERAL_W_POLICY = "regular_forcing_initial_guess_then_hicar_projection"


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def validate_boundary_sequence(
    paths: list[Path],
    *,
    maximum_interval_seconds: float | None = None,
    minimum_states: int = 2,
) -> dict[str, object]:
    """Require a strictly ordered, schema-identical sequence suitable for bracketing."""
    if minimum_states < 1:
        raise ValueError("minimum_states must be positive")
    if len(paths) < minimum_states:
        raise ValueError(
            f"lateral-boundary validation requires at least {minimum_states} state(s)"
        )
    records: list[dict[str, object]] = []
    reference: dict[str, tuple[tuple[str, ...], tuple[int, ...], str]] | None = None
    reference_points: dict[str, np.ndarray] | None = None
    reference_contract: tuple[str, ...] | None = None
    reference_geometry: dict[str, str] | None = None
    previous: dt.datetime | None = None
    intervals: list[float] = []
    for path in paths:
        with netCDF4.Dataset(path) as dataset:
            if str(getattr(dataset, "product_type", "")) != "hicar_lateral_boundary_state":
                raise ValueError(f"{path}: not a HICAR lateral-boundary state")
            valid_time = str(getattr(dataset, "valid_time", ""))
            if not valid_time:
                raise ValueError(f"{path}: missing valid_time")
            when = _timestamp(valid_time)
            if previous is not None:
                interval = (when - previous).total_seconds()
                if interval <= 0.0:
                    raise ValueError("boundary valid times are not strictly increasing")
                if maximum_interval_seconds is not None and interval > maximum_interval_seconds:
                    raise ValueError(
                        f"boundary interval {interval}s exceeds {maximum_interval_seconds}s"
                    )
                intervals.append(interval)
            previous = when
            schema = {
                name: (
                    tuple(variable.dimensions),
                    tuple(variable.shape),
                    str(variable.dtype),
                )
                for name, variable in dataset.variables.items()
            }
            exact_variables = {
                "row",
                "column",
                "relaxation_weight",
                "T",
                "P",
                "QV",
                "QC",
                "QI",
                "HFL",
                "HHL",
            }
            if set(schema) != exact_variables:
                extra = sorted(set(schema) - exact_variables)
                missing = sorted(exact_variables - set(schema))
                raise ValueError(
                    f"{path}: sparse LBC variables differ from the scalar mass-grid contract; "
                    f"missing={missing}, extra={extra}"
                )
            required_dimensions = {
                "T": ("level", "boundary_point"),
                "P": ("level", "boundary_point"),
                "QV": ("level", "boundary_point"),
                "QC": ("level", "boundary_point"),
                "QI": ("level", "boundary_point"),
                "HFL": ("level", "boundary_point"),
                "HHL": ("half_level", "boundary_point"),
            }
            for name, dimensions in required_dimensions.items():
                if name not in dataset.variables:
                    raise ValueError(f"{path}: missing required boundary field {name}")
                if tuple(dataset[name].dimensions) != dimensions:
                    raise ValueError(
                        f"{path}: {name} dimensions {dataset[name].dimensions} != {dimensions}"
                    )
            nx = int(getattr(dataset, "domain_nx", 0))
            ny = int(getattr(dataset, "domain_ny", 0))
            if nx <= 0 or ny <= 0:
                raise ValueError(f"{path}: invalid or missing domain_nx/domain_ny")
            if str(getattr(dataset, "lateral_w_policy", "")) != EXPECTED_LATERAL_W_POLICY:
                raise ValueError(f"{path}: unsupported lateral_w_policy")
            point_rows = np.asarray(dataset["row"][:], dtype=np.int64)
            point_columns = np.asarray(dataset["column"][:], dtype=np.int64)
            if np.any((point_rows < 0) | (point_rows >= ny)) or np.any(
                (point_columns < 0) | (point_columns >= nx)
            ):
                raise ValueError(f"{path}: mass-grid point index is out of bounds")
            if np.unique(np.stack((point_rows, point_columns), axis=1), axis=0).shape[0] != point_rows.size:
                raise ValueError(f"{path}: duplicate mass-grid boundary points")
            if reference is None:
                reference = schema
                reference_points = {
                    name: np.asarray(dataset[name][:], dtype=np.int64)
                    for name in ("row", "column")
                }
                reference_contract = tuple(
                    str(getattr(dataset, name, ""))
                    for name in (
                        "hicar_water_conversion",
                        "lateral_w_policy",
                        "target_grid_fingerprint",
                        "static_sha256",
                        "relaxation_profile",
                        "relaxation_update",
                        "relaxation_timescale_seconds",
                    )
                )
                reference_geometry = {
                    name: hashlib.sha256(
                        np.ascontiguousarray(np.asarray(dataset[name][:])).view(np.uint8)
                    ).hexdigest()
                    for name in ("HFL", "HHL")
                }
            elif schema != reference:
                raise ValueError(f"{path}: boundary variable schema changed across time")
            else:
                for name, expected in reference_points.items():
                    if not np.array_equal(dataset[name][:], expected):
                        raise ValueError(f"{path}: {name} point set changed across time")
                contract = tuple(
                    str(getattr(dataset, name, ""))
                    for name in (
                        "hicar_water_conversion",
                        "lateral_w_policy",
                        "target_grid_fingerprint",
                        "static_sha256",
                        "relaxation_profile",
                        "relaxation_update",
                        "relaxation_timescale_seconds",
                    )
                )
                if contract != reference_contract:
                    raise ValueError(f"{path}: boundary operator contract changed across time")
                for name, expected in reference_geometry.items():
                    actual = hashlib.sha256(
                        np.ascontiguousarray(np.asarray(dataset[name][:])).view(np.uint8)
                    ).hexdigest()
                    if actual != expected:
                        raise ValueError(f"{path}: {name} geometry changed across time")
            for name, variable in dataset.variables.items():
                if name in {"row", "column"}:
                    continue
                values = np.asarray(np.ma.asarray(variable[:]).filled(np.nan))
                if values.dtype.kind in {"f", "c"} and not np.isfinite(values).all():
                    raise ValueError(f"{path}: {name} contains non-finite boundary values")
            weights = np.asarray(dataset["relaxation_weight"][:], dtype=np.float64)
            if np.any((weights < 0.0) | (weights > 1.0)):
                raise ValueError(f"{path}: relaxation_weight must lie in [0, 1]")
            if not np.any(weights == 1.0):
                raise ValueError(f"{path}: relaxation_weight does not constrain the outer edge")
            if str(getattr(dataset, "hicar_water_conversion", "")) not in {
                "APPLIED_JOINT_ALL_WATER_SPECIES",
                "NOT_APPLIED_RESEARCH_PRODUCT",
            }:
                raise ValueError(f"{path}: unknown water-representation contract")
            records.append(
                {
                    "path": str(path),
                    "sha256": sha256(path),
                    "valid_time": when.isoformat().replace("+00:00", "Z"),
                }
            )
    return {
        "schema": "hicarprep-boundary-sequence-v1",
        "state_count": len(records),
        "first_valid_time": records[0]["valid_time"],
        "last_valid_time": records[-1]["valid_time"],
        "minimum_interval_seconds": min(intervals) if intervals else None,
        "maximum_interval_seconds": max(intervals) if intervals else None,
        "sequence_identity": manifest_identity(*paths),
        "states": records,
        "runtime_semantics": "bracket consecutive target-native states; no extrapolation",
    }


def write_boundary_sequence_manifest(
    paths: list[Path],
    output_path: Path,
    *,
    maximum_interval_seconds: float | None = None,
) -> dict[str, object]:
    payload = validate_boundary_sequence(
        paths,
        maximum_interval_seconds=maximum_interval_seconds,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".partial", dir=output_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return payload
