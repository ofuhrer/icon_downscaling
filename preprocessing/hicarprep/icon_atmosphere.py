"""Strict streaming decoder for operational ICON REA-L native-grid atmosphere GRIB."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable, Sequence

import netCDF4
import numpy as np


@dataclass(frozen=True)
class FieldSpec:
    name: str
    param_id: int
    levels: int
    level_type: str
    units: tuple[str, ...]
    minimum: float
    maximum: float


FULL_LEVEL_SPECS = (
    FieldSpec("P", 500001, 80, "generalVerticalLayer", ("pa",), 1.0, 120_000.0),
    FieldSpec("T", 500014, 80, "generalVerticalLayer", ("k",), 150.0, 350.0),
    FieldSpec("U", 500028, 80, "generalVerticalLayer", ("ms-1",), -200.0, 200.0),
    FieldSpec("V", 500030, 80, "generalVerticalLayer", ("ms-1",), -200.0, 200.0),
    FieldSpec("QV", 500035, 80, "generalVerticalLayer", ("kgkg-1",), 0.0, 0.1),
    FieldSpec("QC", 500100, 80, "generalVerticalLayer", ("kgkg-1",), 0.0, 0.1),
)
HALF_LEVEL_SPECS = (
    FieldSpec("W", 500032, 81, "generalVertical", ("ms-1",), -100.0, 100.0),
    FieldSpec("HHL", 500008, 81, "generalVertical", ("m",), -1_000.0, 30_000.0),
)
SURFACE_SPECS = (
    FieldSpec("HSURF", 500007, 1, "surface", ("m",), -500.0, 9_000.0),
    FieldSpec("FR_LAND", 500054, 1, "surface", ("proportion", "1"), 0.0, 1.0),
)


def metadata(field, key: str):
    try:
        return field.metadata(key)
    except (KeyError, RuntimeError, ValueError):
        try:
            values = field.metadata()
        except TypeError:
            return None
        return values.get(key) if isinstance(values, dict) else None


def normalized_uuid(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "")


def normalized_units(value: object) -> str:
    text = str(value or "").strip().lower().replace("**", "").replace("^", "")
    return re.sub(r"[\s*/]+", "", text)


def iso_utc(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def grib_time(field, date_key: str, time_key: str) -> str:
    date = metadata(field, date_key)
    time = metadata(field, time_key)
    if date is None or time is None:
        raise ValueError(f"GRIB message lacks {date_key}/{time_key}")
    parsed = dt.datetime.strptime(f"{int(date):08d}{int(time):04d}", "%Y%m%d%H%M")
    return parsed.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def grib_step_hours(value: object) -> int:
    """Normalize earthkit/ecCodes step metadata to an exact integer hour."""
    if isinstance(value, (int, np.integer)):
        return int(value)
    text = str(value or "").strip().lower()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([smhd]?)", text)
    if match is None:
        raise ValueError(f"unsupported GRIB step representation {value!r}")
    magnitude = float(match.group(1))
    unit = match.group(2) or "h"
    hours = magnitude * {"s": 1.0 / 3600.0, "m": 1.0 / 60.0, "h": 1.0, "d": 24.0}[unit]
    rounded = round(hours)
    if abs(hours - rounded) > 1.0e-9:
        raise ValueError(f"GRIB step {value!r} is not an exact number of hours")
    return int(rounded)


def field_grid_uuid(field) -> str:
    direct = normalized_uuid(metadata(field, "uuidOfHGrid"))
    if direct:
        return direct
    grid = dict(field.geography.grid_spec())
    return normalized_uuid(grid.get("uid", grid.get("uuid", "")))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_grib_fields(path: Path):
    # Payload decoding is handled by ecCodes through earthkit-data. MIR is an
    # interpolation library and is deliberately not a runtime dependency of
    # this native-grid decoder; the operational FDB uenv does not ship its
    # Python bindings.
    import earthkit.data as ekd

    return ekd.from_source("file", str(path))


def _message_identity(field) -> tuple[str, int]:
    return str(metadata(field, "shortName") or "").upper(), int(metadata(field, "paramId"))


def _validate_message(
    field,
    spec: FieldSpec,
    requested_valid_time: str,
    *,
    expected_step: int | None,
    expected_reference_time: str | None,
    expected_uuid: str | None,
) -> dict[str, object]:
    name, param_id = _message_identity(field)
    if name != spec.name or param_id != spec.param_id:
        raise ValueError(
            f"expected {spec.name}/{spec.param_id}, found {name or '<missing>'}/{param_id}"
        )
    units = normalized_units(metadata(field, "units"))
    if units not in spec.units:
        raise ValueError(f"{spec.name} has incompatible units {metadata(field, 'units')!r}")
    level_type = str(metadata(field, "typeOfLevel") or "")
    if level_type != spec.level_type:
        raise ValueError(f"{spec.name} has incompatible typeOfLevel={level_type!r}")
    if str(metadata(field, "stepType") or "").lower() != "instant":
        raise ValueError(f"{spec.name} is not an instantaneous state")
    valid_time = grib_time(field, "validityDate", "validityTime")
    if valid_time != requested_valid_time:
        raise ValueError(f"{spec.name} valid time {valid_time} differs from {requested_valid_time}")
    reference_time = grib_time(field, "dataDate", "dataTime")
    if expected_reference_time is not None and reference_time != expected_reference_time:
        raise ValueError(f"{spec.name} mixes reference cycles")
    step = grib_step_hours(metadata(field, "step"))
    reference = dt.datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
    valid = dt.datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    elapsed_hours = (valid - reference).total_seconds() / 3600.0
    if elapsed_hours != step:
        raise ValueError(
            f"{spec.name} step={step} h disagrees with its reference/valid timestamps"
        )
    if expected_step is not None and step != expected_step:
        raise ValueError(f"{spec.name} has step={step}, expected {expected_step}")
    uuid = field_grid_uuid(field)
    if not uuid:
        raise ValueError(f"{spec.name} lacks a native-grid UUID")
    if expected_uuid is not None and uuid != expected_uuid:
        raise ValueError(f"{spec.name} is on grid {uuid}, expected {expected_uuid}")
    return {"valid_time": valid_time, "reference_time": reference_time, "step": step, "uuid": uuid}


def index_inventory(
    fields: Iterable,
    specs: Sequence[FieldSpec],
    requested_valid_time: str,
    *,
    expected_step: int | None = None,
    expected_reference_time: str | None = None,
    expected_uuid: str | None = None,
) -> tuple[dict[str, dict[int, object]], dict[str, object]]:
    """Validate exact field/level inventory without decoding payload arrays."""
    valid_time = iso_utc(requested_valid_time)
    by_identity = {(spec.name, spec.param_id): spec for spec in specs}
    inventory: dict[str, dict[int, object]] = {spec.name: {} for spec in specs}
    common: dict[str, object] | None = None
    count = 0
    for field in fields:
        identity = _message_identity(field)
        if identity not in by_identity:
            raise ValueError(f"unexpected atmospheric GRIB message {identity[0]}/{identity[1]}")
        spec = by_identity[identity]
        record = _validate_message(
            field,
            spec,
            valid_time,
            expected_step=expected_step,
            expected_reference_time=expected_reference_time,
            expected_uuid=expected_uuid,
        )
        if common is None:
            common = record
        else:
            for key in ("valid_time", "reference_time", "step", "uuid"):
                if record[key] != common[key]:
                    raise ValueError(f"GRIB messages do not share one {key}")
        level = 0 if spec.levels == 1 else int(metadata(field, "level"))
        if level in inventory[spec.name]:
            raise ValueError(f"duplicate {spec.name} level {level}")
        inventory[spec.name][level] = field
        count += 1
    for spec in specs:
        expected = {0} if spec.levels == 1 else set(range(1, spec.levels + 1))
        actual = set(inventory[spec.name])
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"{spec.name} level inventory mismatch; missing={missing}, extra={extra}")
    if common is None:
        raise ValueError("atmospheric GRIB inventory is empty")
    common["message_count"] = count
    return inventory, common


def decode_values(
    field,
    spec: FieldSpec,
    cell_count: int,
    *,
    range_support_indices: np.ndarray | None = None,
) -> np.ndarray:
    raw = np.ma.asarray(field.to_numpy(flatten=True))
    if np.ma.isMaskedArray(raw) and np.any(np.ma.getmaskarray(raw)):
        raise ValueError(f"{spec.name} contains bitmap/missing values")
    values = np.asarray(raw, dtype=np.float64).reshape(-1)
    if values.size != cell_count:
        raise ValueError(f"{spec.name} has {values.size} cells, expected {cell_count}")
    if not np.isfinite(values).all():
        raise ValueError(f"{spec.name} contains non-finite values")
    checked = values if range_support_indices is None else values[range_support_indices]
    if np.any((checked < spec.minimum) | (checked > spec.maximum)):
        scope = "all source cells" if range_support_indices is None else "required source support"
        raise ValueError(
            f"{spec.name} lies outside conservative range {spec.minimum}..{spec.maximum} "
            f"on {scope}"
        )
    return values


def _cell_variable(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values = np.asarray(np.ma.asarray(dataset[name][:]).filled(np.nan), dtype=np.float64).squeeze()
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError(f"EXTPAR {name} is not one finite cell vector")
    return values


def native_storage_options(compression_level: int) -> dict[str, object]:
    """Return lossless NetCDF options for the ephemeral native adapter."""
    if isinstance(compression_level, bool) or not isinstance(compression_level, int):
        raise ValueError("native compression level must be an integer")
    if not 0 <= compression_level <= 9:
        raise ValueError("native compression level must lie in 0..9")
    return (
        {"zlib": True, "complevel": compression_level, "shuffle": True}
        if compression_level
        else {}
    )


def decode_icon_atmosphere(
    dynamic_grib: Path,
    geometry_grib: Path,
    icon_extpar: Path,
    valid_time: str,
    output: Path,
    *,
    missing_qi_policy: str = "error",
    compression_level: int = 1,
    range_support_weights: Path | None = None,
) -> dict[str, object]:
    """Decode one operational REA-L valid time into canonical native ICON NetCDF."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if missing_qi_policy not in {"error", "source-absent-zero"}:
        raise ValueError("unknown missing-QI policy")
    compression = native_storage_options(compression_level)
    requested = iso_utc(valid_time)
    dynamic, dynamic_contract = index_inventory(
        read_grib_fields(dynamic_grib), (*FULL_LEVEL_SPECS, HALF_LEVEL_SPECS[0]), requested
    )
    reference_time = str(dynamic_contract["reference_time"])
    step = int(dynamic_contract["step"])
    uuid = str(dynamic_contract["uuid"])
    geometry, geometry_contract = index_inventory(
        read_grib_fields(geometry_grib), (HALF_LEVEL_SPECS[1], *SURFACE_SPECS), reference_time,
        expected_step=0,
        expected_reference_time=reference_time,
        expected_uuid=uuid,
    )
    if step < 0:
        raise ValueError("forecast step must be nonnegative")
    if missing_qi_policy == "error":
        raise ValueError(
            "operational REA-L does not archive QI; select missing_qi_policy='source-absent-zero' "
            "to materialize an explicit zero target tracer"
        )
    with netCDF4.Dataset(icon_extpar) as extpar:
        extpar_uuid = normalized_uuid(extpar.getncattr("uuidOfHGrid"))
        if extpar_uuid != uuid:
            raise ValueError(f"EXTPAR grid UUID {extpar_uuid} differs from GRIB {uuid}")
        clat = _cell_variable(extpar, "clat")
        clon = _cell_variable(extpar, "clon")
    cell_count = clat.size
    if clon.size != cell_count:
        raise ValueError("EXTPAR clat/clon sizes differ")
    if np.any((clat < -np.pi / 2.0) | (clat > np.pi / 2.0)):
        raise ValueError("EXTPAR clat is outside radian latitude range")
    if np.any((clon < -2.0 * np.pi) | (clon > 2.0 * np.pi)):
        raise ValueError("EXTPAR clon is outside radian longitude range")

    w_range_support = None
    range_support_weights_sha256 = ""
    if range_support_weights is not None:
        from .remap import RBFWeights, grid_fingerprint

        weights = RBFWeights.read(range_support_weights)
        source_fingerprint = grid_fingerprint(clat, clon)
        if weights.source_fingerprint != source_fingerprint:
            raise ValueError("range-support weights do not belong to the native ICON grid")
        w_range_support = np.unique(weights.donor_index)
        if w_range_support.size == 0 or int(w_range_support[-1]) >= cell_count:
            raise ValueError("range-support weights contain invalid native source indices")
        range_support_weights_sha256 = sha256(range_support_weights)

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    source_level = np.arange(80, 0, -1, dtype=np.int32)
    source_half_level = np.arange(81, 0, -1, dtype=np.int32)
    try:
        with netCDF4.Dataset(temporary, "w") as target:
            target.createDimension("cell", cell_count)
            target.createDimension("level", 80)
            target.createDimension("half_level", 81)
            target.createVariable("clat", "f8", ("cell",))[:] = clat
            target["clat"].units = "radian"
            target.createVariable("clon", "f8", ("cell",))[:] = clon
            target["clon"].units = "radian"
            target.createVariable("source_level", "i4", ("level",))[:] = source_level
            target.createVariable("source_half_level", "i4", ("half_level",))[:] = (
                source_half_level
            )
            output_variables = {}
            for spec in FULL_LEVEL_SPECS:
                variable = target.createVariable(
                    spec.name,
                    "f8",
                    ("level", "cell"),
                    chunksizes=(1, cell_count),
                    **compression,
                )
                variable.units = str(metadata(dynamic[spec.name][1], "units"))
                output_variables[spec.name] = variable
            qi = target.createVariable(
                "QI", "f8", ("level", "cell"), chunksizes=(1, cell_count), **compression
            )
            qi.units = "kg kg-1"
            qi.source_policy = "source_absent_zero"
            w = target.createVariable(
                "W",
                "f8",
                ("half_level", "cell"),
                chunksizes=(1, cell_count),
                **compression,
            )
            w.units = str(metadata(dynamic["W"][1], "units"))
            hhl = target.createVariable(
                "HHL", "f8", ("half_level", "cell"),
                chunksizes=(1, cell_count),
                **compression,
            )
            hhl.units = str(metadata(geometry["HHL"][1], "units"))
            surface_variables = {}
            for spec in SURFACE_SPECS:
                variable = target.createVariable(spec.name, "f8", ("cell",), **compression)
                variable.units = str(metadata(geometry[spec.name][0], "units"))
                surface_variables[spec.name] = variable

            previous_pressure = None
            for out_level, source in enumerate(source_level):
                water_sum = np.zeros(cell_count, dtype=np.float64)
                for spec in FULL_LEVEL_SPECS:
                    values = decode_values(dynamic[spec.name][int(source)], spec, cell_count)
                    output_variables[spec.name][out_level, :] = values
                    if spec.name in {"QV", "QC"}:
                        water_sum += values
                    if spec.name == "P":
                        if previous_pressure is not None and np.any(values >= previous_pressure):
                            raise ValueError("P is not strictly decreasing upward after reversal")
                        previous_pressure = values
                if np.any(water_sum >= 1.0):
                    raise ValueError("QV+QC is not a physical moist-air mass fraction")
                qi[out_level, :] = 0.0

            previous_hhl = None
            w_source_minimum = np.inf
            w_source_maximum = -np.inf
            w_outside_support_range_count = 0
            for out_level, source in enumerate(source_half_level):
                w_values = decode_values(
                    dynamic["W"][int(source)],
                    HALF_LEVEL_SPECS[0],
                    cell_count,
                    range_support_indices=w_range_support,
                )
                w_source_minimum = min(w_source_minimum, float(np.min(w_values)))
                w_source_maximum = max(w_source_maximum, float(np.max(w_values)))
                if w_range_support is not None:
                    w_outside_support_range_count += int(
                        np.count_nonzero(
                            (w_values < HALF_LEVEL_SPECS[0].minimum)
                            | (w_values > HALF_LEVEL_SPECS[0].maximum)
                        )
                    )
                hhl_values = decode_values(
                    geometry["HHL"][int(source)], HALF_LEVEL_SPECS[1], cell_count
                )
                if previous_hhl is not None and np.any(hhl_values <= previous_hhl):
                    raise ValueError("HHL is not strictly increasing upward after reversal")
                previous_hhl = hhl_values
                w[out_level, :] = w_values
                hhl[out_level, :] = hhl_values
            surface_values = {}
            for spec in SURFACE_SPECS:
                values = decode_values(geometry[spec.name][0], spec, cell_count)
                surface_variables[spec.name][:] = values
                surface_values[spec.name] = values
            bottom_hhl = np.asarray(hhl[0, :])
            if np.max(np.abs(bottom_hhl - surface_values["HSURF"])) > 1.0:
                raise ValueError("bottom HHL and HSURF differ by more than 1 m")

            target.product_type = "icon_native_atmospheric_state"
            target.valid_time = requested
            target.reference_time = reference_time
            target.forecast_step_hours = step
            target.horizontal_grid_uuid = uuid
            target.vertical_order = "bottom_to_top"
            target.missing_qi_policy = "source_absent_zero"
            target.missing_source_hydrometeors = "QI,QR,QS,QG"
            target.native_storage = (
                f"lossless deflate level {compression_level} with shuffle"
                if compression_level
                else "uncompressed"
            )
            target.dynamic_grib_sha256 = sha256(dynamic_grib)
            target.geometry_grib_sha256 = sha256(geometry_grib)
            target.icon_extpar_sha256 = sha256(icon_extpar)
            target.w_conservative_range_validation = (
                "all_source_cells"
                if w_range_support is None
                else "target_scalar_rbf_donor_support"
            )
            target.w_conservative_range_support_count = (
                cell_count if w_range_support is None else int(w_range_support.size)
            )
            target.w_source_global_minimum_ms = w_source_minimum
            target.w_source_global_maximum_ms = w_source_maximum
            target.w_source_outside_support_range_count = w_outside_support_range_count
            if range_support_weights is not None:
                target.range_support_weights_sha256 = range_support_weights_sha256
            target.field_contract_json = json.dumps(
                {
                    "full_level": {spec.name: spec.param_id for spec in FULL_LEVEL_SPECS},
                    "half_level": {spec.name: spec.param_id for spec in HALF_LEVEL_SPECS},
                    "surface": {spec.name: spec.param_id for spec in SURFACE_SPECS},
                },
                sort_keys=True,
            )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "output": str(output),
        "valid_time": requested,
        "reference_time": reference_time,
        "forecast_step_hours": step,
        "horizontal_grid_uuid": uuid,
        "cell_count": cell_count,
        "missing_qi_policy": missing_qi_policy,
        "compression_level": compression_level,
        "w_conservative_range_validation": (
            "all_source_cells"
            if w_range_support is None
            else "target_scalar_rbf_donor_support"
        ),
        "w_conservative_range_support_count": (
            cell_count if w_range_support is None else int(w_range_support.size)
        ),
        "w_source_global_minimum_ms": w_source_minimum,
        "w_source_global_maximum_ms": w_source_maximum,
        "w_source_outside_support_range_count": w_outside_support_range_count,
        "range_support_weights_sha256": range_support_weights_sha256,
        "dynamic_message_count": dynamic_contract["message_count"],
        "geometry_message_count": geometry_contract["message_count"],
    }
