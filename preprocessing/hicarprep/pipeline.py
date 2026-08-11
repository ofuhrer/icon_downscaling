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

from .products import PRODUCT_VERSION, sha256
from .external import evaluate_external_fields
from .remap import (
    RBFWeights,
    VectorRBFWeights,
    coordinates_in_degrees,
    grid_fingerprint,
)
from .sst import load_target_sst
from .vertical import (
    adjust_vertical_velocity,
    interpolate_interface_w_to_hfl,
    interpolate_height_profile,
    reconstruct_column_state,
)


REQUIRED_FULL_LEVEL_FIELDS = ("T", "P", "QV")
REQUIRED_HYDROMETEORS = ("QC", "QI")
OPTIONAL_HYDROMETEORS = ("QR", "QS", "QG")


def forcing_geometry_for_serialization(
    hhl: np.ndarray, hfl: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return float32 forcing geometry with guaranteed top-level parent cover.

    HICAR reconstructs its SLEVE coordinate in single precision.  Re-evaluating
    the same expression can put the child top one float32 ULP above the static
    value rounded from float64.  Raising only the serialized forcing top by one
    ULP prevents vertical-LUT clamping without changing the authoritative state
    geometry used by hicarprep.
    """
    serialized_hhl = np.asarray(hhl, dtype=np.float32)
    serialized_hfl = np.asarray(hfl, dtype=np.float32).copy()
    if serialized_hfl.ndim != 3 or serialized_hfl.shape[0] < 1:
        raise ValueError("forcing HFL must have at least one three-dimensional level")
    serialized_hfl[-1] = np.nextafter(
        serialized_hfl[-1], np.float32(np.inf)
    )
    return serialized_hhl, serialized_hfl
WATER_FIELDS = ("QV", "QC", "QI", "QR", "QS", "QG")
MINIMUM_REMAPPED_LAYER_THICKNESS_M = 20.0


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


def _mass_grid_wind(value: np.ndarray, *, component: str, target_shape: tuple[int, int]) -> np.ndarray:
    """Return a mass-grid wind field from a mass- or native-face field."""
    levels, ny, nx = value.shape
    target_ny, target_nx = target_shape
    if (ny, nx) == target_shape:
        return np.asarray(value, dtype=np.float64)
    if component == "U" and (ny, nx) == (target_ny, target_nx + 1):
        return 0.5 * (value[:, :, :-1] + value[:, :, 1:])
    if component == "V" and (ny, nx) == (target_ny + 1, target_nx):
        return 0.5 * (value[:, :-1, :] + value[:, 1:, :])
    raise ValueError(
        f"{component} shape {value.shape} is neither mass-grid nor native-face "
        f"for target {target_shape}"
    )


def _face_grid_wind(value: np.ndarray, *, component: str, target_shape: tuple[int, int]) -> np.ndarray:
    """Place a target mass-grid wind on HICAR's native Arakawa-C face grid.

    HICAR performs the same geometric operation when a regular forcing file
    supplies mass-grid U/V: interior faces are midway between adjacent mass
    points and the two exterior faces use the nearest available target value.
    Sparse LBCs bypass that forcing interpolation, so they must carry these
    face values and their own support indices explicitly.
    """
    values = np.asarray(value, dtype=np.float64)
    levels, ny, nx = values.shape
    target_ny, target_nx = target_shape
    if component == "U":
        if (ny, nx) == (target_ny, target_nx + 1):
            return values
        if (ny, nx) != target_shape:
            raise ValueError(f"U shape {values.shape} is incompatible with target {target_shape}")
        result = np.empty((levels, target_ny, target_nx + 1), dtype=np.float64)
        result[:, :, 1:-1] = 0.5 * (values[:, :, :-1] + values[:, :, 1:])
        result[:, :, 0] = values[:, :, 0]
        result[:, :, -1] = values[:, :, -1]
        return result
    if component == "V":
        if (ny, nx) == (target_ny + 1, target_nx):
            return values
        if (ny, nx) != target_shape:
            raise ValueError(f"V shape {values.shape} is incompatible with target {target_shape}")
        result = np.empty((levels, target_ny + 1, target_nx), dtype=np.float64)
        result[:, 1:-1, :] = 0.5 * (values[:, :-1, :] + values[:, 1:, :])
        result[:, 0, :] = values[:, 0, :]
        result[:, -1, :] = values[:, -1, :]
        return result
    raise ValueError(f"unknown wind component {component!r}")


def write_hicar_forcing_record(
    path: Path,
    state: dict[str, np.ndarray],
    diagnostics: dict[str, float | int | str],
    *,
    static_path: Path,
    source_path: Path,
    target_sst_path: Path,
) -> None:
    """Write one target-grid HICAR forcing/clock record from a hicarprep state.

    The regular HICAR forcing reader is still required to initialize the root
    atmospheric state and advance forcing-event time.  This record prevents
    that interface from silently falling back to a separately regridded
    atmosphere. Lateral relaxation remains authoritative in the
    sparse-LBC sequence produced from the same transformed state.
    """
    required = {"T", "P", "QV", "QC", "QI", "U", "V", "W", "HHL", "HFL", "lat", "lon"}
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"HICAR forcing record lacks target fields: {missing}")
    valid_time = str(diagnostics.get("valid_time", ""))
    if not valid_time or valid_time == "unknown":
        raise ValueError("HICAR forcing record requires an unambiguous valid_time")
    when = _normalized_time(valid_time)
    lat = np.asarray(state["lat"], dtype=np.float64)
    lon = np.asarray(state["lon"], dtype=np.float64)
    if lat.ndim != 2 or lon.shape != lat.shape:
        raise ValueError("target latitude/longitude must share one two-dimensional shape")
    ny, nx = lat.shape
    temperature = np.asarray(state["T"], dtype=np.float64)
    if temperature.ndim != 3 or temperature.shape[1:] != (ny, nx):
        raise ValueError("target T must have shape (level, y, x)")
    levels = temperature.shape[0]
    hhl = np.asarray(state["HHL"], dtype=np.float64)
    hfl = np.asarray(state["HFL"], dtype=np.float64)
    if hhl.shape != (levels + 1, ny, nx) or hfl.shape != temperature.shape:
        raise ValueError("target HHL/HFL shapes do not match the atmospheric state")
    payloads = {
        "P": np.asarray(state["P"], dtype=np.float64),
        "T": temperature,
        "QV": np.asarray(state["QV"], dtype=np.float64),
        "QC": np.asarray(state["QC"], dtype=np.float64),
        "QI": np.asarray(state["QI"], dtype=np.float64),
        "U": _mass_grid_wind(np.asarray(state["U"], dtype=np.float64), component="U", target_shape=(ny, nx)),
        "V": _mass_grid_wind(np.asarray(state["V"], dtype=np.float64), component="V", target_shape=(ny, nx)),
        "W": np.asarray(state["W"], dtype=np.float64),
    }
    for name in OPTIONAL_HYDROMETEORS:
        if name in state:
            payloads[name] = np.asarray(state[name], dtype=np.float64)
    for name, values in payloads.items():
        if values.shape != temperature.shape:
            raise ValueError(f"target {name} must have shape {temperature.shape}")
        if not np.all(np.isfinite(values)):
            raise ValueError(f"target {name} contains non-finite values")
    if not np.all(np.isfinite(hhl)) or not np.all(np.diff(hhl, axis=0) > 0.0):
        raise ValueError("target HHL must be finite and strictly bottom-to-top")

    with netCDF4.Dataset(static_path) as static:
        static_lat = np.asarray(static["lat"][:], dtype=np.float64)
        static_lon = np.asarray(static["lon"][:], dtype=np.float64)
        static_hhl = np.asarray(static["HHL"][:], dtype=np.float64)
        static_hfl = np.asarray(static["HFL"][:], dtype=np.float64)
        terrain = np.asarray(static["topo"][:], dtype=np.float64)
        land_fraction = np.asarray(
            static["land_fraction"][:] if "land_fraction" in static.variables else static["landmask"][:],
            dtype=np.float64,
        )
        target_land = np.asarray(
            static["landmask"][:] if "landmask" in static.variables else land_fraction,
            dtype=np.float64,
        ) >= 0.5
    if not np.array_equal(lat, static_lat) or not np.array_equal(lon, static_lon):
        raise ValueError("forcing state and static horizontal grids do not match exactly")
    if not np.array_equal(hhl, static_hhl) or not np.array_equal(hfl, static_hfl):
        raise ValueError("forcing state must use the authoritative static HHL/HFL geometry")
    if terrain.shape != (ny, nx) or land_fraction.shape != (ny, nx):
        raise ValueError("static terrain/land fraction shape does not match forcing grid")
    static_digest = sha256(static_path)
    sst = load_target_sst(
        target_sst_path,
        static_path=static_path,
        valid_time=valid_time,
        target_lat=lat,
        target_lon=lon,
        target_land=target_land,
        static_digest=static_digest,
    )
    serialized_hhl, serialized_hfl = forcing_geometry_for_serialization(hhl, hfl)

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w") as dataset:
            dataset.createDimension("y_1", ny)
            dataset.createDimension("x_1", nx)
            dataset.createDimension("z", levels)
            dataset.createDimension("z_hl", levels + 1)
            dataset.createDimension("time", None)
            dataset.createVariable("lat_1", "f8", ("y_1", "x_1"), zlib=True)[:] = lat
            dataset.createVariable("lon_1", "f8", ("y_1", "x_1"), zlib=True)[:] = lon
            dataset["lat_1"].units = "degrees_north"
            dataset["lon_1"].units = "degrees_east"
            dataset.createVariable("HHL", "f4", ("z_hl", "y_1", "x_1"), zlib=True)[:] = (
                serialized_hhl
            )
            dataset.createVariable("HFL", "f4", ("z", "y_1", "x_1"), zlib=True)[:] = (
                serialized_hfl
            )
            dataset.createVariable("HSURF", "f4", ("y_1", "x_1"), zlib=True)[:] = terrain
            dataset.createVariable("FR_LAND", "f4", ("y_1", "x_1"), zlib=True)[:] = land_fraction
            sst_variable = dataset.createVariable(
                "SST", "f4", ("time", "y_1", "x_1"), zlib=True
            )
            sst_variable[0] = sst
            sst_variable.units = "K"
            sst_variable.hicar_support = "water cells"
            for name, values in payloads.items():
                variable = dataset.createVariable(
                    name, "f4", ("time", "z", "y_1", "x_1"), zlib=True
                )
                variable[0] = values
            time = dataset.createVariable("time", "f8", ("time",))
            time.units = "seconds since 1970-01-01 00:00:00 UTC"
            time.calendar = "gregorian"
            time[0] = when.timestamp()
            for name in ("HHL", "HFL", "HSURF"):
                dataset[name].units = "m"
            for name in ("P",):
                dataset[name].units = "Pa"
            dataset["T"].units = "K"
            for name in ("QV", "QC", "QI", *OPTIONAL_HYDROMETEORS):
                if name in dataset.variables:
                    dataset[name].units = "kg kg-1 dry air"
            for name in ("U", "V", "W"):
                dataset[name].units = "m s-1"
            dataset.product_type = "hicarprep_target_forcing_record"
            dataset.hicarprep_product_version = PRODUCT_VERSION
            dataset.valid_time = when.isoformat().replace("+00:00", "Z")
            dataset.water_representation = "dry-air mixing ratio"
            dataset.wind_representation = (
                "earth-relative U/V and terrain-adjusted W on exact target HFL mass levels; "
                "HICAR performs final grid rotation and variational projection"
            )
            dataset.lateral_relaxation_authority = "hicarprep sparse_lbc_file_list"
            dataset.source_path = str(source_path)
            dataset.source_sha256 = sha256(source_path)
            dataset.static_sha256 = static_digest
            dataset.sst_source_sha256 = sha256(target_sst_path)
            dataset.target_grid_fingerprint = grid_fingerprint(lat, lon)
            dataset.geometry_serialization = "static_sleve_with_one_ulp_top_cover"
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
            _level_cell(dataset, "U", ("level", "full_level"), ("m s-1", "m/s")),
            monotone=True,
        ), operator.apply(
            _level_cell(dataset, "V", ("level", "full_level"), ("m s-1", "m/s")),
            monotone=True,
        )
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


def _remap_vertical_interfaces(
    native_hhl: np.ndarray,
    weights: RBFWeights,
    *,
    minimum_layer_thickness_m: float = MINIMUM_REMAPPED_LAYER_THICKNESS_M,
) -> tuple[np.ndarray, dict[str, float | int | str]]:
    """Remap bottom-to-top interfaces without allowing layer crossings.

    RBF weights may be negative, so applying them independently to every
    interface does not preserve the ordering that is present in each native
    ICON column.  Remap the two endpoints and the strictly positive native
    layer thicknesses with donor-range clipping, then rescale the thicknesses
    to span the remapped endpoints.  Layers below the configured floor are
    raised to it and their deficit is removed proportionally from the excess
    above that floor in thicker layers.  This retains the RBF endpoint
    estimates and most of the vertical distribution without sorting
    interfaces.
    """
    native = np.asarray(native_hhl, dtype=np.float64)
    if native.ndim != 2:
        raise ValueError("native ICON HHL must have interface and cell dimensions")
    if not np.isfinite(minimum_layer_thickness_m) or minimum_layer_thickness_m <= 0.0:
        raise ValueError("minimum remapped layer thickness must be positive and finite")
    native_thickness = np.diff(native, axis=0)
    if not np.isfinite(native).all() or not np.all(native_thickness > 0.0):
        raise ValueError("native ICON HHL must be finite and strictly bottom-to-top")

    bottom = weights.apply(native[0], monotone=True)
    top = weights.apply(native[-1], monotone=True)
    endpoint_span = top - bottom
    thickness = weights.apply(native_thickness, monotone=True)
    thickness_sum = np.sum(thickness, axis=0)
    if (
        not np.isfinite(bottom).all()
        or not np.isfinite(top).all()
        or not np.isfinite(thickness).all()
        or np.any(endpoint_span <= 0.0)
        or np.any(thickness <= 0.0)
        or np.any(thickness_sum <= 0.0)
    ):
        raise ValueError("constrained ICON HHL remap did not produce positive target columns")

    layer_count = native.shape[0] - 1
    required_span = layer_count * minimum_layer_thickness_m
    if np.any(endpoint_span < required_span):
        raise ValueError(
            "remapped ICON column is too shallow for the minimum layer-thickness constraint"
        )

    scale = endpoint_span / thickness_sum
    thickness *= scale[None, ...]
    thin = thickness < minimum_layer_thickness_m
    deficit = np.sum(
        np.where(thin, minimum_layer_thickness_m - thickness, 0.0), axis=0
    )
    excess = np.where(thin, 0.0, thickness - minimum_layer_thickness_m)
    total_excess = np.sum(excess, axis=0)
    if np.any(deficit > total_excess + 1.0e-8):
        raise ValueError("minimum layer-thickness redistribution has insufficient excess")
    reduction = np.divide(
        deficit,
        total_excess,
        out=np.zeros_like(deficit),
        where=total_excess > 0.0,
    )
    thickness = np.where(
        thin,
        minimum_layer_thickness_m,
        minimum_layer_thickness_m + excess * (1.0 - reduction[None, ...]),
    )
    remapped = np.empty((native.shape[0], *weights.target_shape), dtype=np.float64)
    remapped[0] = bottom
    remapped[1:] = bottom[None, ...] + np.cumsum(thickness, axis=0)
    remapped[-1] = top
    differences = np.diff(remapped, axis=0)
    if not np.isfinite(remapped).all() or np.any(
        differences < minimum_layer_thickness_m - 1.0e-8
    ):
        raise ValueError(
            "constrained ICON HHL remap violated the minimum layer-thickness constraint"
        )
    return remapped, {
        "source_geometry_remap": "minimum_thickness_rescaled_to_rbf_endpoints",
        "source_geometry_minimum_layer_thickness_m": float(minimum_layer_thickness_m),
        "source_geometry_corrected_layer_count": int(np.sum(thin)),
        "source_geometry_min_layer_thickness_m": float(np.min(differences)),
        "source_geometry_max_layer_thickness_m": float(np.max(differences)),
        "source_geometry_min_thickness_scale": float(np.min(scale)),
        "source_geometry_max_thickness_scale": float(np.max(scale)),
    }


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
        target_hfl = np.asarray(static["HFL"][:], dtype=np.float64)
        x = np.asarray(static["x"][:], dtype=np.float64)
        y = np.asarray(static["y"][:], dtype=np.float64)
    if weights.target_fingerprint != grid_fingerprint(target_lat, target_lon):
        raise ValueError("cached weights do not belong to this HICAR target grid")
    ny, nx = target_lat.shape
    if not np.all(np.diff(target_hhl, axis=0) > 0.0):
        raise ValueError("HICAR static HHL must be strictly bottom-to-top")
    if target_hfl.shape != (target_hhl.shape[0] - 1, ny, nx):
        raise ValueError("HICAR static HFL must match the HHL mass-level shape")
    if not np.all(np.isfinite(target_hfl)) or not np.all(np.diff(target_hfl, axis=0) > 0.0):
        raise ValueError("HICAR static HFL must be finite and strictly bottom-to-top")
    with netCDF4.Dataset(source_path) as source:
        source_lat = read_coordinate(source, "clat")
        source_lon = read_coordinate(source, "clon")
        if weights.source_fingerprint != grid_fingerprint(source_lat, source_lon):
            raise ValueError("cached weights do not belong to this ICON source grid")
        native_hhl = _level_cell(
            source, "HHL", ("half_level", "interface"), ("m", "meter", "metre")
        )
        declared_order = (
            str(getattr(source["HHL"], "level_order", getattr(source, "vertical_order", "infer")))
            .strip()
            .lower()
        )
        native_differences = np.diff(native_hhl, axis=0)
        if np.all(native_differences > 0.0):
            source_order = "bottom_to_top"
        elif np.all(native_differences < 0.0):
            source_order = "top_to_bottom"
        else:
            increasing = np.all(native_differences > 0.0, axis=0)
            decreasing = np.all(native_differences < 0.0, axis=0)
            mixed = ~(increasing | decreasing)
            raise ValueError(
                "native ICON HHL is not consistently ordered in every column: "
                f"increasing_columns={int(np.sum(increasing))}, "
                f"decreasing_columns={int(np.sum(decreasing))}, "
                f"mixed_columns={int(np.sum(mixed))}, "
                f"minimum_layer_delta_m={float(np.nanmin(native_differences)):.9g}, "
                f"maximum_layer_delta_m={float(np.nanmax(native_differences)):.9g}, "
                f"nonfinite_deltas={int(np.sum(~np.isfinite(native_differences)))}"
            )
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
            raise ValueError("declared ICON vertical order contradicts native HHL")
        bottom_to_top_hhl = native_hhl if source_order == "bottom_to_top" else native_hhl[::-1]
        source_hhl, geometry_diagnostics = _remap_vertical_interfaces(
            bottom_to_top_hhl, weights
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
        reference_time = str(getattr(source, "reference_time", "unknown"))
        forecast_step_hours = int(getattr(source, "forecast_step_hours", -1))
        missing_qi_policy = str(getattr(source, "missing_qi_policy", "unknown"))
    if source_order == "top_to_bottom":
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
    target_w_mass = interpolate_interface_w_to_hfl(
        target_hhl_m=target_hhl,
        target_hfl_m=target_hfl,
        interface_w_ms=target_w,
    )
    state.update(
        {
            "W": target_w_mass,
            "HHL": target_hhl,
            # HICAR evaluates the nonlinear SLEVE mapping at the mass-level
            # reference height.  Except for a linear mapping, that is not the
            # arithmetic mean of the two surrounding interfaces.  Preserve
            # the authoritative mass geometry generated with the static grid.
            "HFL": target_hfl,
            "lat": target_lat,
            "lon": target_lon,
            "terrain_difference": terrain_differences,
        }
    )
    diagnostics = {
        "valid_time": valid_time,
        "source_grid_uuid": source_uuid,
        "source_reference_time": reference_time,
        "source_forecast_step_hours": forecast_step_hours,
        "missing_qi_policy": missing_qi_policy,
        "source_vertical_order": source_order,
        **geometry_diagnostics,
        "terrain_difference_min_m": float(np.min(terrain_differences)),
        "terrain_difference_max_m": float(np.max(terrain_differences)),
        "below_source_target_levels": below_count,
        "buried_source_levels_removed": buried_count,
        "target_w_vertical_coordinate": "authoritative_static_HFL",
        "terrain_columns_lower": cases["lower"],
        "terrain_columns_matched": cases["matched"],
        "terrain_columns_higher": cases["higher"],
    }
    return state, diagnostics


def _state_dimensions(
    name: str, value: np.ndarray, *, levels: int, ny: int, nx: int
) -> tuple[str, ...]:
    if name in {"lat", "lon", "terrain_difference"}:
        return ("y", "x")
    if name == "U" and value.shape == (levels, ny, nx + 1):
        return ("level", "y", "x_u")
    if name == "V" and value.shape == (levels, ny + 1, nx):
        return ("level", "y_v", "x")
    if name == "HHL" or (name == "W" and value.shape[0] == levels + 1):
        return ("half_level", "y", "x")
    return ("level", "y", "x")


def write_initial_condition(
    path: Path,
    state: dict[str, np.ndarray],
    diagnostics: dict[str, float | int | str],
    *,
    static_path: Path,
    weights: RBFWeights,
    supplemental_fields: dict[str, SupplementalField] | None = None,
    water_representation: str = "ICON tracer mass fraction (specific humidity for QV)",
) -> None:
    """Write a target-grid state for diagnostics and preprocessing experiments."""
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
            dataset.createDimension("half_level", state["HHL"].shape[0])
            if state.get("U", np.empty(0)).shape == (state["T"].shape[0], ny, nx + 1):
                dataset.createDimension("x_u", nx + 1)
            if state.get("V", np.empty(0)).shape == (state["T"].shape[0], ny + 1, nx):
                dataset.createDimension("y_v", ny + 1)
            for name, value in state.items():
                variable = dataset.createVariable(
                    name,
                    "f8",
                    _state_dimensions(
                        name, np.asarray(value), levels=state["T"].shape[0], ny=ny, nx=nx
                    ),
                    zlib=True,
                )
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
            dataset.hicar_pressure_adjustment = "HICARPREP_HYDROSTATIC_RECONSTRUCTION"
            dataset.wind_balance = "SOURCE_NATIVE_REMAPPED"
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


def boundary_relaxation_weights(
    x: np.ndarray, y: np.ndarray, width_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return sparse edge indices and a smooth physical-distance relaxation mask.

    The cosine-squared shoulder is one at the outermost target-grid point and
    decays continuously to zero at ``width_m``.  Storing the coefficients in
    each frame makes the preprocessor, rather than a model-grid cell count,
    authoritative for the lateral relaxation geometry.
    """
    rows, cols = boundary_point_indices(x, y, width_m)
    xx, yy = np.meshgrid(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
    distance = np.minimum.reduce((xx - xx.min(), xx.max() - xx, yy - yy.min(), yy.max() - yy))
    phase = np.clip(distance[rows, cols] / float(width_m), 0.0, 1.0)
    weights = np.cos(0.5 * np.pi * phase) ** 2
    weights[distance[rows, cols] <= 1.0e-6] = 1.0
    weights[phase >= 1.0] = 0.0
    return rows, cols, weights


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
) -> None:
    """Write the scalar mass-grid state relaxed by HICAR's sparse LBC reader.

    Winds deliberately remain under the regular forcing/wind-solver path.  In
    particular, this product must not contain earth-relative U/V that HICAR
    would insert into grid-relative face arrays after the wind projection.
    """
    rows, cols, relaxation_weight = boundary_relaxation_weights(x, y, boundary_width_m)
    sparse_fields = ("T", "P", "QV", "QC", "QI")
    required = {*sparse_fields, "HFL", "HHL"}
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"sparse LBC state lacks required mass-grid fields: {missing}")
    levels, ny, nx = np.asarray(state["T"]).shape
    if (ny, nx) != (len(y), len(x)):
        raise ValueError("sparse LBC state and target x/y dimensions differ")
    for name in (*sparse_fields, "HFL"):
        if np.asarray(state[name]).shape != (levels, ny, nx):
            raise ValueError(f"sparse LBC {name} must use the target mass grid")
    if np.asarray(state["HHL"]).shape != (levels + 1, ny, nx):
        raise ValueError("sparse LBC HHL must use target mass-grid interfaces")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with netCDF4.Dataset(temporary, "w") as dataset:
            dataset.createDimension("boundary_point", rows.size)
            dataset.createDimension("level", levels)
            dataset.createDimension("half_level", state["HHL"].shape[0])
            dataset.createVariable("row", "i4", ("boundary_point",))[:] = rows
            dataset.createVariable("column", "i4", ("boundary_point",))[:] = cols
            mass_weight = dataset.createVariable(
                "relaxation_weight", "f8", ("boundary_point",)
            )
            mass_weight[:] = relaxation_weight
            mass_weight.long_name = "lateral relaxation coefficient on the mass grid"
            for name in (*sparse_fields, "HFL", "HHL"):
                value = np.asarray(state[name], dtype=np.float64)
                dimensions = (
                    ("half_level", "boundary_point")
                    if name == "HHL"
                    else ("level", "boundary_point")
                )
                payload = value[:, rows, cols]
                dataset.createVariable(name, "f8", dimensions, zlib=True)[:] = payload
            dataset.product_type = "hicar_lateral_boundary_state"
            dataset.hicarprep_product_version = PRODUCT_VERSION
            dataset.valid_time = str(valid_time)
            dataset.boundary_width_m = boundary_width_m
            dataset.domain_nx = int(np.asarray(x).size)
            dataset.domain_ny = int(np.asarray(y).size)
            dataset.initial_condition_sha256 = sha256(initial_condition_path)
            with netCDF4.Dataset(initial_condition_path) as initial:
                for attribute in ("static_sha256", "target_grid_fingerprint"):
                    value = getattr(initial, attribute, None)
                    if value is None:
                        raise ValueError(
                            f"{initial_condition_path}: missing required {attribute} provenance"
                        )
                    dataset.setncattr(attribute, value)
            dataset.frame_definition = "distance_to_nearest_domain_edge <= boundary_width_m"
            dataset.relaxation_profile = (
                "cosine_squared(distance_to_nearest_domain_edge / boundary_width_m); "
                "one at the outer edge and zero at boundary_width_m"
            )
            dataset.relaxation_timescale_seconds = 3600.0
            dataset.relaxation_update = (
                "outer edge: exact target assignment; shoulder: "
                "alpha=1-exp(-relaxation_weight*dt/relaxation_timescale_seconds)"
            )
            dataset.sparse_field_contract = "T,P,QV,QC,QI on mass-grid boundary points"
            dataset.temporal_semantics = (
                "instantaneous target-native state; runtime brackets consecutive valid times"
            )
            dataset.hicar_pressure_adjustment = "HICARPREP_HYDROSTATIC_RECONSTRUCTION"
            dataset.wind_balance = "NO_SPARSE_WIND; regular forcing and HICAR wind solver authoritative"
            dataset.water_representation = water_representation
            dataset.hicar_water_conversion = (
                "APPLIED_JOINT_ALL_WATER_SPECIES"
                if water_representation == "dry-air mixing ratio"
                else "NOT_APPLIED_RESEARCH_PRODUCT"
            )
            dataset.authoritative_temporal_basis = "T,P,QV,QC,QI; dependent diagnostics refreshed after interpolation"
            dataset.lateral_w_policy = (
                "regular_forcing_initial_guess_then_hicar_projection"
            )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def manifest_identity(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(sha256(path).encode())
    return digest.hexdigest()
