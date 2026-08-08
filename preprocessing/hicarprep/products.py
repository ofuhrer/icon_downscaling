"""NetCDF product ownership, lifetime partitioning, and atomic publication."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
import re

import netCDF4
import numpy as np

from .geometry import SleveConfig, build_sleve_geometry
from .registry import FieldLifetime, FieldRegistry


PRODUCT_VERSION = "hicarprep-products-v1"


def _normalized_units(value: object) -> str:
    text = str(value).strip().lower().replace("**", "").replace("^", "")
    return re.sub(r"[\s*/]+", "", text)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_attributes(source, target, *, omit: Iterable[str] = ()) -> None:
    omitted = set(omit)
    target.setncatts(
        {name: source.getncattr(name) for name in source.ncattrs() if name not in omitted}
    )


def _create_dimensions(source: netCDF4.Dataset, target: netCDF4.Dataset) -> None:
    for name, dimension in source.dimensions.items():
        target.createDimension(name, None if dimension.isunlimited() else len(dimension))


def _copy_variable(
    source,
    target: netCDF4.Dataset,
    *,
    dimensions: tuple[str, ...] | None = None,
    data: np.ndarray | None = None,
) -> None:
    kwargs = {"zlib": True} if source.ndim > 0 and source.dtype.kind not in {"S", "U"} else {}
    destination = target.createVariable(
        source.name, source.dtype, dimensions or source.dimensions, **kwargs
    )
    _copy_attributes(source, destination, omit=("_FillValue",))
    destination[:] = source[:] if data is None else data


def _temporary_path(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".partial", dir=output.parent
    )
    os.close(descriptor)
    Path(name).unlink()
    return Path(name)


def _copy_runtime_base(source: Path, target: Path) -> None:
    """Copy a large static file, bypassing flaky filesystem sendfile paths."""
    try:
        shutil.copy2(source, target)
    except OSError:
        # Some GPFS client/kernel combinations intermittently fail the
        # sendfile fast path with EIO even though ordinary buffered reads and
        # writes remain healthy. Reopen with truncation so no partial prefix
        # from the failed fast copy survives.
        with source.open("rb") as reader, target.open("wb") as writer:
            shutil.copyfileobj(reader, writer, length=16 * 1024 * 1024)
        shutil.copystat(source, target)


def partition_domain_inputs(
    source_path: Path,
    *,
    static_path: Path,
    external_path: Path,
    initial_surface_path: Path,
    epoch_valid_from: dt.datetime,
    initial_valid_time: dt.datetime | None = None,
    registry: FieldRegistry | None = None,
) -> dict[str, list[str]]:
    """Split a geographic source file into lifetime-correct HICAR products."""
    registry = registry or FieldRegistry.default()
    outputs = {
        FieldLifetime.INVARIANT: static_path,
        FieldLifetime.EPOCH: external_path,
        FieldLifetime.CLIMATOLOGY: external_path,
        FieldLifetime.TIME_SERIES: external_path,
        FieldLifetime.INITIAL_ONLY: initial_surface_path,
    }
    variables: dict[FieldLifetime, list[str]] = {lifetime: [] for lifetime in FieldLifetime}
    temporaries = {path: _temporary_path(path) for path in set(outputs.values())}
    datasets: dict[Path, netCDF4.Dataset] = {}
    try:
        with netCDF4.Dataset(source_path) as source:
            for path, temporary in temporaries.items():
                dataset = netCDF4.Dataset(temporary, "w")
                datasets[path] = dataset
                _create_dimensions(source, dataset)
                _copy_attributes(source, dataset)
                dataset.hicarprep_product_version = PRODUCT_VERSION
                dataset.field_registry_version = registry.version
                dataset.source_domain_sha256 = sha256(source_path)
                if path == static_path:
                    dataset.product_type = "immutable_target_geometry"
                elif path == external_path:
                    dataset.product_type = "time_varying_external_parameters"
                    dataset.epoch_valid_from = epoch_valid_from.astimezone(
                        dt.timezone.utc
                    ).isoformat()
                    if "epoch" not in dataset.dimensions:
                        dataset.createDimension("epoch", None)
                    epoch = dataset.createVariable("epoch_time", "f8", ("epoch",))
                    epoch[:] = epoch_valid_from.astimezone(dt.timezone.utc).timestamp()
                    epoch.units = "seconds since 1970-01-01 00:00:00 UTC"
                    epoch.calendar = "proleptic_gregorian"
                    epoch.hicar_lifetime = FieldLifetime.EPOCH.value
                    epoch.hicar_interpolation = "step"
                    epoch.hicar_support = "coordinate"
                    variables[FieldLifetime.EPOCH].append("epoch_time")
                else:
                    dataset.product_type = "initial_surface_state"
                    initial_time = initial_valid_time or epoch_valid_from
                    dataset.valid_time = initial_time.astimezone(dt.timezone.utc).isoformat()
            for name, variable in source.variables.items():
                attributes = {key: variable.getncattr(key) for key in variable.ncattrs()}
                spec = registry.classify(name, attributes)
                destination = datasets[outputs[spec.lifetime]]
                if spec.lifetime is FieldLifetime.EPOCH and "epoch" not in variable.dimensions:
                    _copy_variable(
                        variable,
                        destination,
                        dimensions=("epoch", *variable.dimensions),
                        data=np.expand_dims(variable[:], 0),
                    )
                else:
                    _copy_variable(variable, destination)
                destination[name].hicar_lifetime = spec.lifetime.value
                destination[name].hicar_interpolation = spec.interpolation
                destination[name].hicar_support = spec.support
                variables[spec.lifetime].append(name)
        for dataset in datasets.values():
            dataset.close()
        datasets.clear()
        for path, temporary in temporaries.items():
            os.replace(temporary, path)
    except Exception:
        for dataset in datasets.values():
            dataset.close()
        for temporary in temporaries.values():
            temporary.unlink(missing_ok=True)
        raise
    return {lifetime.value: sorted(names) for lifetime, names in variables.items()}


def append_sleve_geometry(
    static_path: Path,
    *,
    config: SleveConfig = SleveConfig(),
    terrain_name: str = "topo",
) -> None:
    """Append authoritative target HHL/HFL and geometry diagnostics to a static product."""
    temporary = _temporary_path(static_path)
    try:
        _copy_runtime_base(static_path, temporary)
        with netCDF4.Dataset(temporary, "a") as dataset:
            if terrain_name not in dataset.variables:
                raise KeyError(f"static product lacks authoritative terrain {terrain_name!r}")
            terrain = np.asarray(dataset[terrain_name][:], dtype=np.float64)
            geometry = build_sleve_geometry(terrain, config)
            for name, size in (("level", config.nz), ("half_level", config.nz + 1)):
                if name not in dataset.dimensions:
                    dataset.createDimension(name, size)
                elif len(dataset.dimensions[name]) != size:
                    raise ValueError(f"existing {name} dimension has incompatible size")
            if "reference_layer" not in dataset.dimensions:
                dataset.createDimension("reference_layer", config.nz)
            definitions = {
                "terrain_large_scale": (("y", "x"), "m"),
                "terrain_small_scale": (("y", "x"), "m"),
                "HHL": (("half_level", "y", "x"), "m"),
                "HFL": (("level", "y", "x"), "m"),
                "SLEVE_JACOBIAN": (("level", "y", "x"), "1"),
                "LAYER_THICKNESS": (("level", "y", "x"), "m"),
                "reference_layer_thickness": (("reference_layer",), "m"),
            }
            for name, (dimensions, units) in definitions.items():
                if name in dataset.variables:
                    raise ValueError(f"static product already contains {name}")
                variable = dataset.createVariable(name, "f8", dimensions, zlib=True)
                variable[:] = geometry[name]
                variable.units = units
                variable.hicar_lifetime = FieldLifetime.INVARIANT.value
                variable.level_order = "bottom_to_top" if "level" in " ".join(dimensions) else ""
            dataset.sleve_operator = "HICAR domain_obj.setup_sleve / auto_level=1"
            dataset.sleve_nz = config.nz
            dataset.sleve_model_top_m = config.model_top_m
            dataset.sleve_lowest_layer_m = config.lowest_layer_m
            dataset.sleve_stretch_factor = config.stretch_factor
            dataset.sleve_decay_rates = f"{config.decay_rate_large},{config.decay_rate_small}"
            dataset.sleve_exponent = config.exponent
            dataset.required_minimum_sleve_jacobian = config.minimum_jacobian
            dataset.required_minimum_sleve_layer_thickness_m = (
                config.minimum_layer_thickness_m
            )
            dataset.minimum_sleve_jacobian = float(np.min(geometry["SLEVE_JACOBIAN"]))
            dataset.minimum_sleve_layer_thickness_m = float(np.min(geometry["LAYER_THICKNESS"]))
        os.replace(temporary, static_path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_product_lifetimes(path: Path, registry: FieldRegistry | None = None) -> None:
    registry = registry or FieldRegistry.default()
    with netCDF4.Dataset(path) as dataset:
        product_type = str(getattr(dataset, "product_type", ""))
        allowed = {
            "immutable_target_geometry": {FieldLifetime.INVARIANT},
            "time_varying_external_parameters": {
                FieldLifetime.EPOCH,
                FieldLifetime.CLIMATOLOGY,
                FieldLifetime.TIME_SERIES,
            },
            "initial_surface_state": {
                FieldLifetime.INITIAL_ONLY,
                # Layer/depth coordinates may be repeated in a self-describing
                # initial-state product, but invariant spatial fields may not.
                FieldLifetime.INVARIANT,
            },
        }
        if product_type not in allowed:
            raise ValueError(f"unknown or missing hicarprep product_type in {path}")
        required_by_product = {
            "immutable_target_geometry": {
                "x",
                "y",
                "lat",
                "lon",
                "topo",
                "HHL",
                "HFL",
                "SLEVE_JACOBIAN",
                "LAYER_THICKNESS",
            },
            "time_varying_external_parameters": {"epoch_time"},
            "initial_surface_state": set(),
        }
        missing = required_by_product[product_type] - set(dataset.variables)
        if missing:
            raise ValueError(f"{path}: {product_type} lacks required fields {sorted(missing)}")
        for name, variable in dataset.variables.items():
            spec = registry.classify(
                name, {key: variable.getncattr(key) for key in variable.ncattrs()}
            )
            if spec.units is not None:
                declared_units = getattr(variable, "units", None)
                if declared_units is None:
                    raise ValueError(f"{path}: {name} lacks required units {spec.units!r}")
                if _normalized_units(declared_units) != _normalized_units(spec.units):
                    raise ValueError(
                        f"{path}: {name} units {declared_units!r} differ from registry "
                        f"units {spec.units!r}"
                    )
            if spec.lifetime not in allowed[product_type]:
                raise ValueError(
                    f"{path}: {name} lifetime {spec.lifetime.value} is invalid in {product_type}"
                )
            if (
                product_type == "initial_surface_state"
                and spec.lifetime is FieldLifetime.INVARIANT
                and spec.support != "coordinate"
            ):
                raise ValueError(
                    f"{path}: only invariant coordinates may be repeated in initial surface state"
                )
            if spec.lifetime is FieldLifetime.INVARIANT and any(
                dim in variable.dimensions for dim in ("time", "month", "epoch")
            ):
                raise ValueError(f"{path}: invariant field {name} has a temporal dimension")
            if spec.lifetime is FieldLifetime.CLIMATOLOGY:
                month_dims = [dim for dim in variable.dimensions if dim == "month"]
                if not month_dims or len(dataset.dimensions["month"]) != 12:
                    raise ValueError(f"{path}: climatological field {name} requires 12 months")
            if spec.lifetime is FieldLifetime.EPOCH and "epoch" not in variable.dimensions:
                raise ValueError(f"{path}: epoch field {name} requires an epoch dimension")
            if spec.lifetime is FieldLifetime.TIME_SERIES and spec.support != "coordinate":
                if "time" not in variable.dimensions:
                    raise ValueError(f"{path}: time-series field {name} requires a time dimension")


def validate_product_set(
    static_path: Path,
    external_path: Path,
    initial_surface_path: Path,
    registry: FieldRegistry | None = None,
) -> None:
    """Validate target-grid identity and coupled land/surface closure across products."""
    registry = registry or FieldRegistry.default()
    for path in (static_path, external_path, initial_surface_path):
        validate_product_lifetimes(path, registry)
    with (
        netCDF4.Dataset(static_path) as static,
        netCDF4.Dataset(external_path) as external,
        netCDF4.Dataset(initial_surface_path) as surface,
    ):
        source_hashes = {
            str(getattr(dataset, "source_domain_sha256", ""))
            for dataset in (static, external, surface)
        }
        if len(source_hashes) != 1 or "" in source_hashes:
            raise ValueError("domain products do not share one source-domain identity")
        target_shape = (len(static.dimensions["y"]), len(static.dimensions["x"]))
        for label, dataset in (("external", external), ("initial surface", surface)):
            shape = (len(dataset.dimensions["y"]), len(dataset.dimensions["x"]))
            if shape != target_shape:
                raise ValueError(f"{label} grid {shape} differs from static grid {target_shape}")

        land = np.asarray(static["landmask"][:], dtype=np.float64)
        if land.shape != target_shape or not np.isfinite(land).all():
            raise ValueError("authoritative landmask is non-finite or has the wrong shape")
        if np.any(land < 0.0) or np.any(land > 1.0):
            raise ValueError("authoritative landmask lies outside [0, 1]")
        land_boolean = land >= 0.5
        if "soil_type" in static.variables:
            soil = np.asarray(static["soil_type"][:])
            if not np.isfinite(soil[land_boolean]).all():
                raise ValueError("target soil type is non-finite over land")

        for name in ("glacier_fraction", "urban_fraction"):
            if name in external.variables:
                values = np.asarray(external[name][:], dtype=np.float64)
                if not np.isfinite(values).all() or np.any(values < 0.0) or np.any(values > 1.0):
                    raise ValueError(f"{name} is not a finite fraction")
        if "glacier_fraction" in external.variables and "urban_fraction" in external.variables:
            combined = np.asarray(external["glacier_fraction"][:]) + np.asarray(
                external["urban_fraction"][:]
            )
            if np.any(combined > 1.0 + 1.0e-8):
                raise ValueError("glacier and urban fractions overlap beyond one target cell")

        if "landuse_fraction" in external.variables:
            variable = external["landuse_fraction"]
            category_axes = [
                index
                for index, name in enumerate(variable.dimensions)
                if name not in {"epoch", "y", "x"}
            ]
            if len(category_axes) != 1:
                raise ValueError("landuse_fraction requires exactly one category dimension")
            values = np.asarray(variable[:], dtype=np.float64)
            if not np.isfinite(values).all() or np.any(values < 0.0) or np.any(values > 1.0):
                raise ValueError("landuse_fraction contains invalid fractions")
            closure = np.sum(values, axis=category_axes[0])
            if not np.allclose(closure, 1.0, atol=1.0e-6):
                raise ValueError("landuse fractions do not close to one")

        if not str(getattr(surface, "valid_time", "")):
            raise ValueError("initial surface state lacks valid_time")
        for name in ("soil_temperature", "soil_vwc"):
            if name in surface.variables:
                values = np.asarray(surface[name][:], dtype=np.float64)
                if not np.isfinite(values[..., land_boolean]).all():
                    raise ValueError(f"{name} is non-finite over target land")


def _write_runtime_field(
    dataset: netCDF4.Dataset,
    name: str,
    values: np.ndarray,
    dimensions: tuple[str, ...],
    *,
    units: str,
    long_name: str,
) -> None:
    """Create or replace one HICAR domain field without changing its grid."""
    values = np.asarray(values)
    expected = tuple(len(dataset.dimensions[dimension]) for dimension in dimensions)
    if values.shape != expected:
        raise ValueError(f"{name} has shape {values.shape}, expected {expected}")
    if name in dataset.variables:
        variable = dataset[name]
        if variable.dimensions != dimensions or variable.shape != expected:
            raise ValueError(
                f"existing {name} has incompatible dimensions {variable.dimensions} and shape "
                f"{variable.shape}"
            )
    else:
        variable = dataset.createVariable(name, "f4", dimensions, zlib=values.ndim > 0)
    variable[:] = values
    variable.units = units
    variable.long_name = long_name
    variable.hicar_lifetime = FieldLifetime.INITIAL_ONLY.value


def _normalize_external_runtime_field(
    name: str, values: np.ndarray
) -> tuple[np.ndarray, str | None]:
    """Normalize optional land climatologies to HICAR's runtime conventions."""
    numeric = np.asarray(values)
    if name in {"VEGFRA", "vegetation_fraction_max"}:
        numeric = np.asarray(numeric, dtype=np.float64)
        if not np.isfinite(numeric).all() or np.any(numeric < 0.0):
            raise ValueError(f"external {name} must be finite and non-negative")
        conversion = None
        if numeric.size and float(np.max(numeric)) <= 1.0:
            numeric = numeric * 100.0
            conversion = "fraction_to_percent"
        if np.any(numeric > 100.0):
            raise ValueError(f"external {name} lies outside 0..100 percent")
        return numeric, conversion
    if name == "LAI":
        numeric = np.asarray(numeric, dtype=np.float64)
        if not np.isfinite(numeric).all() or np.any((numeric < 0.0) | (numeric > 20.0)):
            raise ValueError("external LAI lies outside the conservative 0..20 range")
    elif name == "ALBEDO":
        numeric = np.asarray(numeric, dtype=np.float64)
        if not np.isfinite(numeric).all() or np.any(numeric < 0.0):
            raise ValueError("external ALBEDO must be finite and non-negative")
        conversion = None
        if numeric.size and float(np.max(numeric)) > 1.0:
            if np.any(numeric > 100.0):
                raise ValueError("external ALBEDO lies outside 0..100 percent")
            numeric = numeric / 100.0
            conversion = "percent_to_fraction"
        return numeric, conversion
    return numeric, None


def validate_hicar_runtime_domain(path: Path) -> None:
    """Validate the single-file land-state contract read by HICAR today."""
    required_2d = {
        "lat",
        "lon",
        "topo",
        "landmask",
        "landuse",
        "soil_type",
        "surface_temperature",
        "soil_deep_temperature",
        "swe",
        "snow_height",
    }
    required_3d = {"soil_temperature", "soil_vwc"}
    with netCDF4.Dataset(path) as dataset:
        missing = sorted((required_2d | required_3d) - set(dataset.variables))
        if missing:
            raise ValueError(f"HICAR runtime domain lacks required fields {missing}")
        if not str(getattr(dataset, "land_state_valid_time", "")):
            raise ValueError("HICAR runtime domain lacks land_state_valid_time")
        target_shape = (len(dataset.dimensions["y"]), len(dataset.dimensions["x"]))
        if "level" not in dataset.dimensions or "half_level" not in dataset.dimensions:
            raise ValueError("HICAR runtime domain lacks atmospheric vertical dimensions")
        levels = len(dataset.dimensions["level"])
        expected_hhl_shape = (levels + 1, *target_shape)
        expected_hfl_shape = (levels, *target_shape)
        if "HHL" not in dataset.variables or dataset["HHL"].shape != expected_hhl_shape:
            raise ValueError(f"HICAR runtime HHL must have shape {expected_hhl_shape}")
        if "HFL" not in dataset.variables or dataset["HFL"].shape != expected_hfl_shape:
            raise ValueError(f"HICAR runtime HFL must have shape {expected_hfl_shape}")
        hhl = np.asarray(dataset["HHL"][:], dtype=np.float64)
        hfl = np.asarray(dataset["HFL"][:], dtype=np.float64)
        thickness = np.diff(hhl, axis=0)
        required_thickness = float(
            getattr(dataset, "required_minimum_sleve_layer_thickness_m", 20.0)
        )
        if not np.isfinite(hhl).all() or not np.isfinite(hfl).all():
            raise ValueError("HICAR runtime vertical geometry contains non-finite values")
        if np.any(thickness <= required_thickness):
            raise ValueError(
                "HICAR runtime vertical geometry violates its minimum layer thickness: "
                f"minimum={float(np.min(thickness)):.9g} m, "
                f"required_above={required_thickness:.9g} m"
            )
        land = np.asarray(dataset["landmask"][:], dtype=np.float64) >= 0.5
        for name in required_2d:
            values = np.asarray(np.ma.asarray(dataset[name][:]).filled(np.nan))
            if values.shape != target_shape:
                raise ValueError(f"{name} has shape {values.shape}, expected {target_shape}")
            if not np.isfinite(values).all():
                raise ValueError(f"{name} contains non-finite values")
        for name in required_3d:
            variable = dataset[name]
            if variable.dimensions != ("soil_layer", "y", "x"):
                raise ValueError(f"{name} must use dimensions soil_layer,y,x")
            values = np.asarray(np.ma.asarray(variable[:]).filled(np.nan), dtype=np.float64)
            if not np.isfinite(values[:, land]).all():
                raise ValueError(f"{name} contains non-finite target-land values")
        surface_temperature = np.asarray(dataset["surface_temperature"][:], dtype=np.float64)
        soil_temperature = np.asarray(dataset["soil_temperature"][:], dtype=np.float64)
        soil_vwc = np.asarray(dataset["soil_vwc"][:], dtype=np.float64)
        if np.any((surface_temperature < 180.0) | (surface_temperature > 350.0)):
            raise ValueError("HICAR runtime surface temperature lies outside 180..350 K")
        if np.any((soil_temperature < 180.0) | (soil_temperature > 340.0)):
            raise ValueError("HICAR runtime soil temperature lies outside 180..340 K")
        if np.any((soil_vwc < 0.0) | (soil_vwc > 1.0)):
            raise ValueError("HICAR runtime total soil water lies outside 0..1 m3 m-3")
        landuse = np.asarray(dataset["landuse"][:], dtype=np.int64)
        glacier = land & (landuse == 24)
        if np.any(soil_vwc[:, glacier] != 0.0):
            raise ValueError("HICAR runtime glacier columns contain porous-soil water")
        swe = np.asarray(dataset["swe"][:], dtype=np.float64)
        snow_height = np.asarray(dataset["snow_height"][:], dtype=np.float64)
        if np.any(swe < 0.0) or np.any(snow_height < 0.0):
            raise ValueError("HICAR runtime snow fields must be non-negative")
        if np.any(swe > 10_000.0) or np.any(snow_height > 20.0):
            raise ValueError("HICAR runtime snow fields exceed conservative physical limits")
        if "snow_density" in dataset.variables:
            density = np.asarray(dataset["snow_density"][:], dtype=np.float64)
            snow = swe > 1.0e-9
            if np.any(snow & ((density <= 0.0) | (density > 917.0))):
                raise ValueError("HICAR runtime positive snow has invalid bulk density")
        if "snow_temperature_initial" in dataset.variables:
            snow_temperature = np.asarray(
                dataset["snow_temperature_initial"][:], dtype=np.float64
            )
            if snow_temperature.shape != target_shape or not np.isfinite(
                snow_temperature
            ).all():
                raise ValueError("HICAR runtime initial snow temperature is invalid")
            snow = swe > 1.0e-9
            upper = np.minimum(surface_temperature, 273.15)
            lower = np.minimum(np.maximum(surface_temperature - 10.0, 180.0), upper)
            # Runtime fields are float32.  Recomputing a bound after the skin
            # temperature and snow temperature have been rounded separately
            # can differ by one float32 ULP (about 3e-5 K here).  Do not reject
            # a value that was clipped to the bound before serialization.
            serialization_tolerance_k = 1.0e-4
            if np.any(
                snow
                & (
                    (snow_temperature < lower - serialization_tolerance_k)
                    | (snow_temperature > upper + serialization_tolerance_k)
                )
            ):
                raise ValueError("HICAR runtime initial snow temperature violates bounds")


def assemble_hicar_runtime_domain(
    static_path: Path,
    surface_path: Path,
    output_path: Path,
    *,
    external_path: Path | None = None,
    valid_time: dt.datetime | None = None,
) -> None:
    """Merge lifetime-separated surface state into HICAR's current domain reader contract."""
    if output_path.exists() or Path(f"{output_path}.ready").exists():
        raise FileExistsError(f"refusing to overwrite published HICAR domain {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ready = Path(f"{output_path}.ready")
    temporary = _temporary_path(output_path)
    try:
        _copy_runtime_base(static_path, temporary)
        with netCDF4.Dataset(surface_path) as surface, netCDF4.Dataset(temporary, "a") as runtime:
            if str(getattr(surface, "product_type", "")) != "initial_surface_state":
                raise ValueError("surface product is not an initial_surface_state")
            if str(getattr(surface, "static_sha256", "")) != sha256(static_path):
                raise ValueError("surface product was not generated for this static domain")
            target_shape = (len(runtime.dimensions["y"]), len(runtime.dimensions["x"]))
            if (len(surface.dimensions["y"]), len(surface.dimensions["x"])) != target_shape:
                raise ValueError("surface product grid differs from static domain")
            if "soil_layer" not in runtime.dimensions:
                runtime.createDimension("soil_layer", len(surface.dimensions["soil_layer"]))
            elif len(runtime.dimensions["soil_layer"]) != len(surface.dimensions["soil_layer"]):
                raise ValueError("surface and static soil-layer counts differ")

            surface_valid_time = dt.datetime.fromisoformat(
                str(surface.valid_time).replace("Z", "+00:00")
            )
            if surface_valid_time.tzinfo is None:
                surface_valid_time = surface_valid_time.replace(tzinfo=dt.timezone.utc)
            materialization_time = valid_time or surface_valid_time
            materialization_time = materialization_time.astimezone(dt.timezone.utc)
            if materialization_time != surface_valid_time.astimezone(dt.timezone.utc):
                raise ValueError("external materialization time must equal surface valid_time")

            if external_path is not None:
                from .external import evaluate_external_fields

                expected_external_sha256 = str(
                    getattr(surface, "external_parameters_sha256", "")
                )
                actual_external_sha256 = sha256(external_path)
                if not expected_external_sha256:
                    raise ValueError(
                        "surface state was not prepared with the supplied external parameters"
                    )
                if expected_external_sha256 != actual_external_sha256:
                    raise ValueError(
                        "surface and runtime assembly use different external parameters"
                    )
                external_backcast = str(
                    getattr(surface, "external_epoch_back_extrapolation", "none")
                )
                if external_backcast not in {"none", "explicit_research_override"}:
                    raise ValueError(
                        f"unknown surface external-epoch policy {external_backcast!r}"
                    )
                evaluated = evaluate_external_fields(
                    external_path,
                    materialization_time,
                    allow_epoch_back_extrapolation=(
                        external_backcast == "explicit_research_override"
                    ),
                )
                with netCDF4.Dataset(external_path) as external:
                    for name, values in evaluated.items():
                        source_variable = external[name]
                        preserve_monthly_vegfrac = name == "VEGFRA" and (
                            "month" in source_variable.dimensions
                        )
                        if preserve_monthly_vegfrac:
                            values = np.asarray(source_variable[:])
                            dimensions = source_variable.dimensions
                        else:
                            dimensions = tuple(
                                dimension
                                for dimension in source_variable.dimensions
                                if dimension not in {"epoch", "month", "time"}
                            )
                        values, unit_conversion = _normalize_external_runtime_field(
                            name, values
                        )
                        for dimension, size in zip(dimensions, np.asarray(values).shape):
                            if dimension not in runtime.dimensions:
                                runtime.createDimension(dimension, size)
                            elif len(runtime.dimensions[dimension]) != size:
                                raise ValueError(
                                    f"external {name} dimension {dimension} differs from runtime"
                                )
                        if name in runtime.variables:
                            destination = runtime[name]
                            if destination.dimensions != dimensions:
                                raise ValueError(
                                    f"external {name} dimensions {dimensions} differ from runtime "
                                    f"{destination.dimensions}"
                                )
                        else:
                            destination = runtime.createVariable(
                                name,
                                source_variable.dtype,
                                dimensions,
                                zlib=np.asarray(values).ndim > 0,
                            )
                            _copy_attributes(source_variable, destination, omit=("_FillValue",))
                        destination[:] = values
                        if name in {"VEGFRA", "vegetation_fraction_max"}:
                            destination.units = "percent"
                        elif name == "ALBEDO":
                            destination.units = "1"
                        if unit_conversion is not None:
                            destination.hicar_unit_conversion = unit_conversion
                        if preserve_monthly_vegfrac:
                            destination.materialization_policy = (
                                "preserved_12_month_climatology_for_hicar_monthly_vegfrac"
                            )
                        else:
                            destination.materialized_valid_time = materialization_time.isoformat()
                runtime.external_parameters_sha256 = actual_external_sha256
                runtime.external_parameters_valid_time = materialization_time.isoformat()
            elif str(getattr(surface, "external_parameters_sha256", "")):
                raise ValueError(
                    "surface state requires its matching external parameters at runtime assembly"
                )

            soil_temperature = np.asarray(surface["soil_temperature"][:], dtype=np.float64)
            mappings = {
                "surface_temperature": (
                    np.asarray(surface["skin_temperature"][:]),
                    ("y", "x"),
                    "K",
                    "initial surface skin temperature",
                ),
                "soil_temperature": (
                    soil_temperature,
                    ("soil_layer", "y", "x"),
                    "K",
                    "initial soil temperature",
                ),
                "soil_vwc": (
                    np.asarray(surface["soil_vwc"][:]),
                    ("soil_layer", "y", "x"),
                    "m3 m-3",
                    "initial total volumetric soil water content",
                ),
                "soil_deep_temperature": (
                    soil_temperature[-1],
                    ("y", "x"),
                    "K",
                    "initial deep soil temperature",
                ),
                "swe": (
                    np.asarray(surface["snow_water_equivalent"][:]),
                    ("y", "x"),
                    "kg m-2",
                    "initial snow water equivalent",
                ),
                "snow_height": (
                    np.asarray(surface["snow_depth"][:]),
                    ("y", "x"),
                    "m",
                    "initial snow depth",
                ),
                "snow_density": (
                    np.asarray(surface["snow_density"][:]),
                    ("y", "x"),
                    "kg m-3",
                    "initial bulk snow density diagnostic",
                ),
                "snow_temperature_initial": (
                    np.asarray(surface["snow_temperature_initial"][:]),
                    ("y", "x"),
                    "K",
                    "initial bulk snow temperature",
                ),
            }
            for name, (values, dimensions, units, long_name) in mappings.items():
                _write_runtime_field(
                    runtime,
                    name,
                    values,
                    dimensions,
                    units=units,
                    long_name=long_name,
                )
            runtime.product_type = "hicar_runtime_domain_initial_conditions"
            runtime.land_state_valid_time = str(surface.valid_time)
            runtime.land_state_soil_water_method = str(surface.soil_water_method)
            runtime.land_state_surface_sha256 = sha256(surface_path)
            runtime.land_state_static_sha256 = sha256(static_path)
            runtime.land_state_source_surface_sha256 = str(surface.source_surface_sha256)
            runtime.land_state_target_soil_type_source = str(
                getattr(surface, "target_soil_type_source", "unknown")
            )
            runtime.land_state_static_epoch_back_extrapolation = str(
                getattr(surface, "static_epoch_back_extrapolation", "unknown")
            )
            runtime.land_state_external_epoch_back_extrapolation = str(
                getattr(surface, "external_epoch_back_extrapolation", "unknown")
            )
            runtime.land_state_external_epoch_valid_from = str(
                getattr(surface, "external_epoch_valid_from", "")
            )
            runtime.land_state_static_landuse_epoch_valid_from = str(
                getattr(surface, "static_landuse_epoch_valid_from", "")
            )
        validate_hicar_runtime_domain(temporary)
        os.replace(temporary, output_path)
        ready.touch()
    finally:
        temporary.unlink(missing_ok=True)
