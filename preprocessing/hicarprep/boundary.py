"""Validation and indexing of target-native HICAR lateral-boundary snapshots."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np

from .pipeline import manifest_identity
from .products import sha256


def _timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def validate_boundary_sequence(
    paths: list[Path],
    *,
    maximum_interval_seconds: float | None = None,
    allow_unbalanced_research: bool = False,
) -> dict[str, object]:
    """Require a strictly ordered, schema-identical sequence suitable for bracketing."""
    if len(paths) < 2:
        raise ValueError("a bracketable lateral-boundary sequence requires at least two states")
    records: list[dict[str, object]] = []
    reference: dict[str, tuple[tuple[str, ...], tuple[int, ...]]] | None = None
    reference_points: tuple[np.ndarray, np.ndarray] | None = None
    reference_contract: tuple[str, str, str, str] | None = None
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
                name: (tuple(variable.dimensions), tuple(variable.shape))
                for name, variable in dataset.variables.items()
            }
            if reference is None:
                reference = schema
                reference_points = (
                    np.asarray(dataset["row"][:], dtype=np.int64),
                    np.asarray(dataset["column"][:], dtype=np.int64),
                )
                reference_contract = tuple(
                    str(getattr(dataset, name, ""))
                    for name in (
                        "hicar_pressure_adjustment",
                        "wind_balance",
                        "hicar_water_conversion",
                        "lateral_w_policy",
                    )
                )
            elif schema != reference:
                raise ValueError(f"{path}: boundary variable schema changed across time")
            else:
                if not np.array_equal(dataset["row"][:], reference_points[0]) or not np.array_equal(
                    dataset["column"][:], reference_points[1]
                ):
                    raise ValueError(f"{path}: boundary point set changed across time")
                contract = tuple(
                    str(getattr(dataset, name, ""))
                    for name in (
                        "hicar_pressure_adjustment",
                        "wind_balance",
                        "hicar_water_conversion",
                        "lateral_w_policy",
                    )
                )
                if contract != reference_contract:
                    raise ValueError(f"{path}: boundary operator contract changed across time")
            for name, variable in dataset.variables.items():
                if name in {"row", "column"}:
                    continue
                values = np.asarray(np.ma.asarray(variable[:]).filled(np.nan))
                if values.dtype.kind in {"f", "c"} and not np.isfinite(values).all():
                    raise ValueError(f"{path}: {name} contains non-finite boundary values")
            if str(getattr(dataset, "hicar_water_conversion", "")) not in {
                "APPLIED_JOINT_ALL_WATER_SPECIES",
                "NOT_APPLIED_RESEARCH_PRODUCT",
            }:
                raise ValueError(f"{path}: unknown water-representation contract")
            if not allow_unbalanced_research and (
                str(getattr(dataset, "hicar_pressure_adjustment", "")) != "APPLIED_HICAR_NATIVE"
                or str(getattr(dataset, "wind_balance", ""))
                != "APPLIED_HICAR_ADJOINT_VARIATIONAL_PROJECTION"
            ):
                raise ValueError(f"{path}: boundary state lacks a HICAR balance certificate")
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
        "minimum_interval_seconds": min(intervals),
        "maximum_interval_seconds": max(intervals),
        "sequence_identity": manifest_identity(*paths),
        "states": records,
        "runtime_semantics": "bracket consecutive target-native states; no extrapolation",
    }


def write_boundary_sequence_manifest(
    paths: list[Path],
    output_path: Path,
    *,
    maximum_interval_seconds: float | None = None,
    allow_unbalanced_research: bool = False,
) -> dict[str, object]:
    payload = validate_boundary_sequence(
        paths,
        maximum_interval_seconds=maximum_interval_seconds,
        allow_unbalanced_research=allow_unbalanced_research,
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
