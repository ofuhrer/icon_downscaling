"""Append and evaluate multi-year external-parameter records."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import shutil
import tempfile

import netCDF4
import numpy as np

from .registry import FieldLifetime, FieldRegistry


def append_epoch(
    external_path: Path,
    source_path: Path,
    *,
    valid_from: dt.datetime,
    registry: FieldRegistry | None = None,
) -> list[str]:
    """Append a piecewise-constant land/ice/urban epoch to an external product."""
    registry = registry or FieldRegistry.default()
    valid_from = valid_from.astimezone(dt.timezone.utc)
    appended: list[str] = []
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{external_path.name}.", suffix=".partial", dir=external_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(external_path, temporary)
        with netCDF4.Dataset(temporary, "a") as destination, netCDF4.Dataset(source_path) as source:
            if str(getattr(destination, "product_type", "")) != "time_varying_external_parameters":
                raise ValueError("destination is not a hicarprep external-parameter product")
            epoch = destination["epoch_time"]
            timestamp = valid_from.timestamp()
            existing = np.asarray(epoch[:], dtype=np.float64)
            if existing.size and timestamp <= existing[-1]:
                raise ValueError("new epoch must be strictly later than existing epochs")
            index = existing.size
            existing_epoch_fields = {
                name
                for name, variable in destination.variables.items()
                if name != "epoch_time"
                and getattr(variable, "hicar_lifetime", "") == FieldLifetime.EPOCH.value
            }
            supplied_epoch_fields: set[str] = set()
            for name, variable in source.variables.items():
                try:
                    spec = registry.classify(
                        name, {key: variable.getncattr(key) for key in variable.ncattrs()}
                    )
                except KeyError:
                    continue
                if spec.lifetime is FieldLifetime.EPOCH:
                    supplied_epoch_fields.add(name)
            missing = existing_epoch_fields - supplied_epoch_fields
            if missing:
                raise ValueError(
                    "epoch append must supply a complete record; missing "
                    + ", ".join(sorted(missing))
                )
            new_fields = supplied_epoch_fields - existing_epoch_fields
            if existing.size and new_fields:
                raise ValueError(
                    "new epoch fields cannot be introduced after the first record: "
                    + ", ".join(sorted(new_fields))
                )
            epoch[index] = timestamp
            for name, variable in source.variables.items():
                try:
                    spec = registry.classify(
                        name, {key: variable.getncattr(key) for key in variable.ncattrs()}
                    )
                except KeyError:
                    continue
                if spec.lifetime is not FieldLifetime.EPOCH:
                    continue
                payload = np.asarray(variable[:])
                if "epoch" in variable.dimensions:
                    if payload.shape[variable.dimensions.index("epoch")] != 1:
                        raise ValueError(f"{name}: epoch append source must contain one record")
                    payload = np.take(payload, 0, axis=variable.dimensions.index("epoch"))
                    dimensions = tuple(dim for dim in variable.dimensions if dim != "epoch")
                else:
                    dimensions = variable.dimensions
                if name not in destination.variables:
                    target = destination.createVariable(
                        name, variable.dtype, ("epoch", *dimensions), zlib=variable.ndim > 0
                    )
                    target.setncatts(
                        {
                            key: variable.getncattr(key)
                            for key in variable.ncattrs()
                            if key != "_FillValue"
                        }
                    )
                    target.hicar_lifetime = FieldLifetime.EPOCH.value
                    target.hicar_interpolation = spec.interpolation
                    target.hicar_support = spec.support
                target = destination[name]
                if target.dimensions != ("epoch", *dimensions):
                    raise ValueError(f"{name}: epoch dimensions changed across source products")
                if target.shape[1:] != payload.shape:
                    raise ValueError(f"{name}: epoch spatial/category shape changed")
                target[index] = payload
                if spec.interpolation == "area_fraction":
                    numeric = np.asarray(payload, dtype=np.float64)
                    if (
                        not np.isfinite(numeric).all()
                        or np.any(numeric < 0.0)
                        or np.any(numeric > 1.0)
                    ):
                        raise ValueError(f"{name}: epoch fractions must be finite and in [0, 1]")
                appended.append(name)
            if not appended:
                raise ValueError("source contains no fields classified with epoch lifetime")
        os.replace(temporary, external_path)
    finally:
        temporary.unlink(missing_ok=True)
    return sorted(appended)


def _timestamp(value: dt.datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).timestamp()


def _month_anchor(year: int, month: int) -> dt.datetime:
    """Return the 15th-of-month anchor used for monthly-mean external fields."""
    return dt.datetime(year, month, 15, tzinfo=dt.timezone.utc)


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    absolute = year * 12 + (month - 1) + offset
    return absolute // 12, absolute % 12 + 1


def evaluate_external_fields(
    path: Path,
    valid_time: dt.datetime,
    *,
    registry: FieldRegistry | None = None,
) -> dict[str, np.ndarray]:
    """Evaluate step epochs, monthly cycles, and linear series at one valid time."""
    registry = registry or FieldRegistry.default()
    result: dict[str, np.ndarray] = {}
    with netCDF4.Dataset(path) as dataset:
        when = _timestamp(valid_time)
        epochs = np.asarray(dataset["epoch_time"][:], dtype=np.float64)
        for name, variable in dataset.variables.items():
            spec = registry.classify(
                name, {key: variable.getncattr(key) for key in variable.ncattrs()}
            )
            if spec.support == "coordinate":
                continue
            data = np.asarray(variable[:])
            if spec.lifetime is FieldLifetime.EPOCH:
                index = int(np.searchsorted(epochs, when, side="right") - 1)
                if index < 0:
                    raise ValueError(f"{name}: no epoch is valid at {valid_time.isoformat()}")
                result[name] = np.take(data, index, axis=variable.dimensions.index("epoch"))
            elif spec.lifetime is FieldLifetime.CLIMATOLOGY:
                axis = variable.dimensions.index("month")
                utc_time = (
                    valid_time.replace(tzinfo=dt.timezone.utc)
                    if valid_time.tzinfo is None
                    else valid_time.astimezone(dt.timezone.utc)
                )
                current_anchor = _month_anchor(utc_time.year, utc_time.month)
                if utc_time >= current_anchor:
                    lower_year, lower_month = utc_time.year, utc_time.month
                    upper_year, upper_month = _shift_month(lower_year, lower_month, 1)
                else:
                    upper_year, upper_month = utc_time.year, utc_time.month
                    lower_year, lower_month = _shift_month(upper_year, upper_month, -1)
                lower_anchor = _month_anchor(lower_year, lower_month)
                upper_anchor = _month_anchor(upper_year, upper_month)
                fraction = (utc_time - lower_anchor) / (upper_anchor - lower_anchor)
                lower = np.take(data, lower_month - 1, axis=axis)
                upper = np.take(data, upper_month - 1, axis=axis)
                result[name] = (1.0 - fraction) * lower + fraction * upper
            elif spec.lifetime is FieldLifetime.TIME_SERIES:
                axis = variable.dimensions.index("time")
                time_var = dataset["time"]
                dates = netCDF4.num2date(
                    time_var[:],
                    time_var.units,
                    calendar=getattr(time_var, "calendar", "standard"),
                    only_use_cftime_datetimes=False,
                )
                timestamps = np.asarray([_timestamp(item) for item in dates])
                upper = int(np.searchsorted(timestamps, when, side="right"))
                if upper == 0 or upper == timestamps.size:
                    if when == timestamps[0]:
                        result[name] = np.take(data, 0, axis=axis)
                        continue
                    if when == timestamps[-1]:
                        result[name] = np.take(data, -1, axis=axis)
                        continue
                    raise ValueError(f"{name}: requested time is outside the external series")
                lower = upper - 1
                weight = (when - timestamps[lower]) / (timestamps[upper] - timestamps[lower])
                result[name] = (1.0 - weight) * np.take(data, lower, axis=axis) + weight * np.take(
                    data, upper, axis=axis
                )
    return result
