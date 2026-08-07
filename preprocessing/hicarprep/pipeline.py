"""End-to-end canonical ICON state transformation and HICAR IC/LBC encoding."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np

from .balance import BalanceCertificate
from .products import PRODUCT_VERSION, sha256
from .external import evaluate_external_fields
from .remap import (
    RBFWeights,
    VectorRBFWeights,
    coordinates_in_degrees,
    grid_fingerprint,
)
from .vertical import (
    adjust_vertical_velocity,
    interpolate_height_profile,
    reconstruct_column_state,
)


REQUIRED_FULL_LEVEL_FIELDS = ("T", "P", "QV")
REQUIRED_HYDROMETEORS = ("QC", "QI")
OPTIONAL_HYDROMETEORS = ("QR", "QS", "QG")
WATER_FIELDS = ("QV", "QC", "QI", "QR", "QS", "QG")


@dataclass(frozen=True)
class SupplementalField:
    values: np.ndarray
    dimensions: tuple[str, ...]
    attributes: dict[str, object]


def _normalized_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def convert_water_to_hicar_mixing_ratios(
    state: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Jointly convert ICON moist-mass fractions to dry-air mixing ratios."""
    names = [name for name in WATER_FIELDS if name in state]
    if "QV" not in names:
        raise ValueError("water conversion requires QV")
    total = sum(
        (np.asarray(state[name], dtype=np.float64) for name in names), np.zeros_like(state["QV"])
    )
    if not np.isfinite(total).all() or np.any(total < 0.0) or np.any(total >= 1.0):
        raise ValueError("ICON water mass fractions do not define a positive dry-air fraction")
    dry_fraction = 1.0 - total
    result = {name: np.asarray(value).copy() for name, value in state.items()}
    for name in names:
        result[name] = np.asarray(state[name], dtype=np.float64) / dry_fraction

    # Preserve the total-density diagnostic exactly under the representation
    # change.  P and T are unchanged; condensate contributes mass but no gas
    # pressure, while vapor contributes with Rv/Rd = 1.608.
    if "RHO" in result:
        total_mixing = sum((result[name] for name in names), np.zeros_like(result["QV"]))
        result["RHO"] = (
            result["P"]
            * (1.0 + total_mixing)
            / (287.05 * result["T"] * (1.0 + 1.608 * result["QV"]))
        )
    return result


def load_valid_time_inputs(
    *,
    valid_time: str,
    target_shape: tuple[int, int],
    surface_path: Path | None = None,
    external_path: Path | None = None,
) -> dict[str, SupplementalField]:
    """Load one exact surface state and evaluate external fields at IC time."""
    when = _normalized_time(valid_time)
    fields: dict[str, SupplementalField] = {}
    if surface_path is not None:
        with netCDF4.Dataset(surface_path) as surface:
            if str(getattr(surface, "product_type", "")) != "initial_surface_state":
                raise ValueError("surface input is not a hicarprep initial_surface_state")
            surface_time = str(getattr(surface, "valid_time", ""))
            if not surface_time or _normalized_time(surface_time) != when:
                raise ValueError("surface state valid_time does not match atmospheric IC")
            for name, variable in surface.variables.items():
                values = np.asarray(np.ma.asarray(variable[:]).filled(np.nan))
                if "y" in variable.dimensions and "x" in variable.dimensions:
                    if values.shape[-2:] != target_shape:
                        raise ValueError(f"surface field {name} is not on the target grid")
                fields[name] = SupplementalField(
                    values=values,
                    dimensions=tuple(variable.dimensions),
                    attributes={key: variable.getncattr(key) for key in variable.ncattrs()},
                )
    if external_path is not None:
        evaluated = evaluate_external_fields(external_path, when)
        with netCDF4.Dataset(external_path) as external:
            for name, values in evaluated.items():
                variable = external[name]
                dimensions = tuple(
                    dimension
                    for dimension in variable.dimensions
                    if dimension not in {"epoch", "month", "time"}
                )
                values = np.asarray(values)
                if "y" in dimensions and "x" in dimensions and values.shape[-2:] != target_shape:
                    raise ValueError(f"external field {name} is not on the target grid")
                if name in fields:
                    raise ValueError(f"field {name} is owned by both surface and external products")
                fields[name] = SupplementalField(
                    values=values,
                    dimensions=dimensions,
                    attributes={key: variable.getncattr(key) for key in variable.ncattrs()},
                )
    return fields


def _read_array(dataset: netCDF4.Dataset, name: str) -> tuple[np.ndarray, tuple[str, ...]]:
    if name not in dataset.variables:
        raise KeyError(f"{dataset.filepath()}: required variable {name!r} is missing")
    variable = dataset[name]
    data = np.asarray(np.ma.asarray(variable[:]).filled(np.nan), dtype=np.float64)
    dimensions = list(variable.dimensions)
    for temporal in ("time", "valid_time"):
        if temporal in dimensions:
            axis = dimensions.index(temporal)
            if data.shape[axis] != 1:
                raise ValueError(f"{name}: canonical input must contain exactly one valid time")
            data = np.take(data, 0, axis=axis)
            dimensions.pop(axis)
    return data, tuple(dimensions)


def read_coordinate(dataset: netCDF4.Dataset, name: str) -> np.ndarray:
    values, _ = _read_array(dataset, name)
    units = getattr(dataset[name], "units", None)
    return coordinates_in_degrees(values, units)


def _level_cell(
    dataset: netCDF4.Dataset,
    name: str,
    level_names: tuple[str, ...],
    accepted_units: tuple[str, ...] | None = None,
) -> np.ndarray:
    data, dimensions = _read_array(dataset, name)
    if accepted_units is not None:
        units = str(getattr(dataset[name], "units", "")).strip().lower()
        if units not in accepted_units:
            raise ValueError(
                f"{name}: units {getattr(dataset[name], 'units', None)!r} are not canonical; "
                f"expected one of {accepted_units}"
            )
    try:
        cell_axis = dimensions.index("cell")
    except ValueError as exc:
        raise ValueError(
            f"{name}: canonical native ICON field requires a 'cell' dimension"
        ) from exc
    level_matches = [dimensions.index(item) for item in level_names if item in dimensions]
    if len(level_matches) != 1 or data.ndim != 2:
        raise ValueError(f"{name}: expected one vertical dimension and cell, got {dimensions}")
    return np.transpose(data, (level_matches[0], cell_axis))


def _source_wind(
    dataset: netCDF4.Dataset,
    operator: RBFWeights,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    vector_operator: VectorRBFWeights | None,
) -> tuple[np.ndarray, np.ndarray]:
    if "U" in dataset.variables and "V" in dataset.variables:
        return operator.apply(
            _level_cell(dataset, "U", ("level", "full_level"), ("m s-1", "m/s"))
        ), operator.apply(_level_cell(dataset, "V", ("level", "full_level"), ("m s-1", "m/s")))
    edge_fields = {"VN", "edge_lat", "edge_lon", "edge_normal_east", "edge_normal_north"}
    if not edge_fields.issubset(dataset.variables):
        raise KeyError(
            "native ICON input requires earth-relative U/V or VN plus edge coordinates "
            "and edge-normal geometry"
        )
    vn_data, vn_dims = _read_array(dataset, "VN")
    vn_units = str(getattr(dataset["VN"], "units", "")).strip().lower()
    if vn_units not in {"m s-1", "m/s"}:
        raise ValueError("VN must use canonical m s-1 units")
    vertical_dims = [name for name in ("level", "full_level") if name in vn_dims]
    if vn_data.ndim != 2 or "edge" not in vn_dims or len(vertical_dims) != 1:
        raise ValueError("VN must have level and edge dimensions")
    vn = np.transpose(vn_data, (vn_dims.index(vertical_dims[0]), vn_dims.index("edge")))
    edge_lat = read_coordinate(dataset, "edge_lat")
    edge_lon = read_coordinate(dataset, "edge_lon")
    normal_east, _ = _read_array(dataset, "edge_normal_east")
    normal_north, _ = _read_array(dataset, "edge_normal_north")
    if vector_operator is None:
        raise ValueError(
            "native VN requires a cached vector RBF operator; run build-vector-weights"
        )
    expected_source = grid_fingerprint(edge_lat, edge_lon, normal_east, normal_north)
    if vector_operator.source_fingerprint != expected_source:
        raise ValueError("cached vector weights do not belong to this ICON edge grid")
    if vector_operator.target_fingerprint != grid_fingerprint(target_lat, target_lon):
        raise ValueError("cached vector weights do not belong to this HICAR target grid")
    return vector_operator.apply(vn)


def transform_icon_state(
    source_path: Path,
    static_path: Path,
    weights: RBFWeights,
    vector_weights: VectorRBFWeights | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, float | int | str]]:
    """Transform one canonical native ICON timestamp to final target columns."""
    with netCDF4.Dataset(static_path) as static:
        target_lat = np.asarray(static["lat"][:], dtype=np.float64)
        target_lon = np.asarray(static["lon"][:], dtype=np.float64)
        target_hhl = np.asarray(static["HHL"][:], dtype=np.float64)
        x = np.asarray(static["x"][:], dtype=np.float64)
        y = np.asarray(static["y"][:], dtype=np.float64)
    if weights.target_fingerprint != grid_fingerprint(target_lat, target_lon):
        raise ValueError("cached weights do not belong to this HICAR target grid")
    ny, nx = target_lat.shape
    if not np.all(np.diff(target_hhl, axis=0) > 0.0):
        raise ValueError("HICAR static HHL must be strictly bottom-to-top")
    with netCDF4.Dataset(source_path) as source:
        source_lat = read_coordinate(source, "clat")
        source_lon = read_coordinate(source, "clon")
        if weights.source_fingerprint != grid_fingerprint(source_lat, source_lon):
            raise ValueError("cached weights do not belong to this ICON source grid")
        source_hhl = weights.apply(
            _level_cell(source, "HHL", ("half_level", "interface"), ("m", "meter", "metre"))
        )
        unit_policy = {
            "T": ("k", "kelvin"),
            "P": ("pa", "pascal", "pascals"),
            "QV": ("kg kg-1", "kg/kg", "1"),
        }
        remapped = {
            name: weights.apply(
                _level_cell(source, name, ("level", "full_level"), unit_policy[name]),
                monotone=True,
            )
            for name in REQUIRED_FULL_LEVEL_FIELDS
        }
        remapped["U"], remapped["V"] = _source_wind(
            source, weights, target_lat, target_lon, vector_weights
        )
        missing_hydrometeors = [
            name for name in REQUIRED_HYDROMETEORS if name not in source.variables
        ]
        if missing_hydrometeors:
            raise KeyError(
                "canonical ICON state is missing required hydrometeors: "
                + ", ".join(missing_hydrometeors)
            )
        hydro = {
            name: weights.apply(
                _level_cell(source, name, ("level", "full_level"), ("kg kg-1", "kg/kg", "1")),
                monotone=True,
            )
            for name in (*REQUIRED_HYDROMETEORS, *OPTIONAL_HYDROMETEORS)
            if name in source.variables
        }
        source_w = weights.apply(
            _level_cell(source, "W", ("half_level", "interface"), ("m s-1", "m/s"))
        )
        valid_time = str(getattr(source, "valid_time", "unknown"))
        source_uuid = str(getattr(source, "horizontal_grid_uuid", weights.source_fingerprint))
        declared_order = (
            str(getattr(source["HHL"], "level_order", getattr(source, "vertical_order", "infer")))
            .strip()
            .lower()
        )

    differences = np.diff(source_hhl, axis=0)
    if np.all(differences > 0.0):
        source_order = "bottom_to_top"
    elif np.all(differences < 0.0):
        source_order = "top_to_bottom"
    else:
        raise ValueError("remapped ICON HHL is not consistently ordered in every column")
    declared_aliases = {
        "bottom_to_top": "bottom_to_top",
        "bottom-to-top": "bottom_to_top",
        "ascending": "bottom_to_top",
        "top_to_bottom": "top_to_bottom",
        "top-to-bottom": "top_to_bottom",
        "descending": "top_to_bottom",
        "infer": source_order,
        "": source_order,
    }
    if declared_order not in declared_aliases:
        raise ValueError(f"unsupported ICON vertical order declaration {declared_order!r}")
    if declared_aliases[declared_order] != source_order:
        raise ValueError("declared ICON vertical order contradicts remapped HHL")
    if source_order == "top_to_bottom":
        source_hhl = source_hhl[::-1]
        source_w = source_w[::-1]
        remapped = {name: value[::-1] for name, value in remapped.items()}
        hydro = {name: value[::-1] for name, value in hydro.items()}

    nz = target_hhl.shape[0] - 1
    state = {
        name: np.empty((nz, ny, nx), dtype=np.float64)
        for name in ("T", "P", "QV", "U", "V", "THETA", "RHO", *hydro)
    }
    target_w = np.empty_like(target_hhl)
    terrain_differences = np.empty((ny, nx), dtype=np.float64)
    below_count = 0
    buried_count = 0
    cases = {"lower": 0, "matched": 0, "higher": 0}
    for row in range(ny):
        for col in range(nx):
            column, diagnostics = reconstruct_column_state(
                source_hhl_m=source_hhl[:, row, col],
                target_hhl_m=target_hhl[:, row, col],
                temperature_k=remapped["T"][:, row, col],
                pressure_pa=remapped["P"][:, row, col],
                qv=remapped["QV"][:, row, col],
                u_ms=remapped["U"][:, row, col],
                v_ms=remapped["V"][:, row, col],
                hydrometeors={name: value[:, row, col] for name, value in hydro.items()},
            )
            for name in state:
                state[name][:, row, col] = column[name]
            target_w[:, row, col] = interpolate_height_profile(
                source_hhl[:, row, col],
                source_w[:, row, col],
                target_hhl[:, row, col],
                lower_gradient_bounds=(0.0, 0.0),
                monotone=True,
            )
            terrain_differences[row, col] = diagnostics.terrain_difference_m
            below_count += diagnostics.below_source_level_count
            buried_count += diagnostics.buried_source_level_count
            cases[diagnostics.terrain_case] += 1

    target_w = adjust_vertical_velocity(
        target_hhl_m=target_hhl,
        interpolated_w_ms=target_w,
        u_ms=state["U"],
        v_ms=state["V"],
        x_m=x,
        y_m=y,
    )
    state.update(
        {
            "W": target_w,
            "HHL": target_hhl,
            "HFL": 0.5 * (target_hhl[:-1] + target_hhl[1:]),
            "lat": target_lat,
            "lon": target_lon,
            "terrain_difference": terrain_differences,
        }
    )
    diagnostics = {
        "valid_time": valid_time,
        "source_grid_uuid": source_uuid,
        "source_vertical_order": source_order,
        "terrain_difference_min_m": float(np.min(terrain_differences)),
        "terrain_difference_max_m": float(np.max(terrain_differences)),
        "below_source_target_levels": below_count,
        "buried_source_levels_removed": buried_count,
        "terrain_columns_lower": cases["lower"],
        "terrain_columns_matched": cases["matched"],
        "terrain_columns_higher": cases["higher"],
    }
    return state, diagnostics


def _state_dimensions(name: str) -> tuple[str, ...]:
    if name in {"lat", "lon", "terrain_difference"}:
        return ("y", "x")
    if name in {"W", "HHL"}:
        return ("half_level", "y", "x")
    return ("level", "y", "x")


def write_initial_condition(
    path: Path,
    state: dict[str, np.ndarray],
    diagnostics: dict[str, float | int | str],
    *,
    static_path: Path,
    weights: RBFWeights,
    allow_unprojected_wind: bool = False,
    supplemental_fields: dict[str, SupplementalField] | None = None,
    water_representation: str = "ICON tracer mass fraction (specific humidity for QV)",
    balance_certificate: BalanceCertificate | None = None,
) -> None:
    """Write a certified target IC or an explicitly marked research product."""
    if balance_certificate is not None:
        balance_certificate.validate(state)
    elif not allow_unprojected_wind:
        raise RuntimeError(
            "HICAR pressure adjustment and variational wind projection have not been applied; "
            "model-ready publication is blocked. "
            "Use allow_unprojected_wind only for preprocessing research tests."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ny, nx = state["lat"].shape
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w") as dataset:
            dataset.createDimension("x", nx)
            dataset.createDimension("y", ny)
            dataset.createDimension("level", state["T"].shape[0])
            dataset.createDimension("half_level", state["W"].shape[0])
            for name, value in state.items():
                variable = dataset.createVariable(name, "f8", _state_dimensions(name), zlib=True)
                variable[:] = value
            for name, field in (supplemental_fields or {}).items():
                if name in dataset.variables:
                    raise ValueError(f"supplemental field {name} conflicts with atmospheric state")
                for dimension, size in zip(field.dimensions, field.values.shape):
                    if dimension not in dataset.dimensions:
                        dataset.createDimension(dimension, size)
                    elif len(dataset.dimensions[dimension]) != size:
                        raise ValueError(
                            f"supplemental dimension {dimension} has incompatible size"
                        )
                variable = dataset.createVariable(
                    name,
                    field.values.dtype,
                    field.dimensions,
                    zlib=field.values.ndim > 0 and field.values.dtype.kind not in {"S", "U"},
                )
                variable[:] = field.values
                variable.setncatts(
                    {key: value for key, value in field.attributes.items() if key != "_FillValue"}
                )
            dataset.product_type = "hicar_initial_condition"
            dataset.hicarprep_product_version = PRODUCT_VERSION
            dataset.static_sha256 = sha256(static_path)
            dataset.horizontal_operator = weights.method
            dataset.source_grid_fingerprint = weights.source_fingerprint
            dataset.target_grid_fingerprint = weights.target_fingerprint
            dataset.valid_time = str(diagnostics["valid_time"])
            dataset.hydrostatic_balance = (
                "continuous target-column trapezoidal log-pressure integration; research only"
            )
            dataset.hicar_pressure_adjustment = (
                "APPLIED_HICAR_NATIVE" if balance_certificate else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.wind_balance = (
                "APPLIED_HICAR_ADJOINT_VARIATIONAL_PROJECTION"
                if balance_certificate
                else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.water_representation = water_representation
            dataset.hicar_water_conversion = (
                "APPLIED_JOINT_ALL_WATER_SPECIES"
                if water_representation == "dry-air mixing ratio"
                else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.surface_state_assembly = (
                "APPLIED_AT_EXACT_VALID_TIME" if supplemental_fields else "NOT_APPLIED"
            )
            dataset.authoritative_atmospheric_basis = (
                "T,P,U,V,W,QV,QC,QI and present QR,QS,QG; THETA and RHO are diagnostics"
            )
            if balance_certificate:
                dataset.balance_certificate_state_fingerprint = (
                    balance_certificate.state_fingerprint
                )
                dataset.balance_producer_commit = balance_certificate.producer_commit
                dataset.maximum_discrete_hydrostatic_residual = (
                    balance_certificate.maximum_discrete_hydrostatic_residual
                )
                dataset.maximum_mass_continuity_residual = (
                    balance_certificate.maximum_mass_continuity_residual
                )
            for name, value in diagnostics.items():
                if name not in {"valid_time"}:
                    dataset.setncattr(name, value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def boundary_point_indices(
    x: np.ndarray, y: np.ndarray, width_m: float
) -> tuple[np.ndarray, np.ndarray]:
    if width_m <= 0.0:
        raise ValueError("boundary width must be positive")
    xx, yy = np.meshgrid(np.asarray(x), np.asarray(y))
    distance = np.minimum.reduce((xx - xx.min(), xx.max() - xx, yy - yy.min(), yy.max() - yy))
    return np.nonzero(distance <= width_m + 1.0e-6)


def write_boundary_condition(
    path: Path,
    state: dict[str, np.ndarray],
    *,
    x: np.ndarray,
    y: np.ndarray,
    boundary_width_m: float,
    initial_condition_path: Path,
    valid_time: str,
    water_representation: str = "ICON tracer mass fraction (specific humidity for QV)",
    allow_unbalanced_state: bool = False,
    include_lateral_w: bool = False,
    balance_certificate: BalanceCertificate | None = None,
) -> None:
    """Extract a sparse physical-distance frame from the identically transformed state."""
    if balance_certificate is not None:
        balance_certificate.validate(state)
    elif not allow_unbalanced_state:
        raise RuntimeError(
            "HICAR pressure adjustment and variational wind projection have not been applied; "
            "lateral-boundary publication is blocked"
        )
    rows, cols = boundary_point_indices(x, y, boundary_width_m)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w") as dataset:
            dataset.createDimension("boundary_point", rows.size)
            dataset.createDimension("level", state["T"].shape[0])
            dataset.createDimension("half_level", state["W"].shape[0])
            dataset.createVariable("row", "i4", ("boundary_point",))[:] = rows
            dataset.createVariable("column", "i4", ("boundary_point",))[:] = cols
            for name, value in state.items():
                if name == "W" and not include_lateral_w:
                    continue
                if name in {"lat", "lon", "terrain_difference"}:
                    dimensions = ("boundary_point",)
                    payload = value[rows, cols]
                elif name in {"W", "HHL"}:
                    dimensions = ("half_level", "boundary_point")
                    payload = value[:, rows, cols]
                else:
                    dimensions = ("level", "boundary_point")
                    payload = value[:, rows, cols]
                dataset.createVariable(name, "f8", dimensions, zlib=True)[:] = payload
            dataset.product_type = "hicar_lateral_boundary_state"
            dataset.hicarprep_product_version = PRODUCT_VERSION
            dataset.valid_time = str(valid_time)
            dataset.boundary_width_m = boundary_width_m
            dataset.initial_condition_sha256 = sha256(initial_condition_path)
            dataset.frame_definition = "distance_to_nearest_domain_edge <= boundary_width_m"
            dataset.temporal_semantics = (
                "instantaneous target-native state; runtime brackets consecutive valid times"
            )
            dataset.hicar_pressure_adjustment = (
                "APPLIED_HICAR_NATIVE" if balance_certificate else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.wind_balance = (
                "APPLIED_HICAR_ADJOINT_VARIATIONAL_PROJECTION"
                if balance_certificate
                else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.water_representation = water_representation
            dataset.hicar_water_conversion = (
                "APPLIED_JOINT_ALL_WATER_SPECIES"
                if water_representation == "dry-air mixing ratio"
                else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.authoritative_temporal_basis = "T,P,U,V,QV,QC,QI and present QR,QS,QG; dependent diagnostics refreshed after interpolation"
            dataset.lateral_w_policy = (
                "relax_projected_interface_w" if include_lateral_w else "diagnose_in_hicar"
            )
            if balance_certificate:
                dataset.balance_certificate_state_fingerprint = (
                    balance_certificate.state_fingerprint
                )
                dataset.balance_producer_commit = balance_certificate.producer_commit
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def manifest_identity(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(sha256(path).encode())
    return digest.hexdigest()
