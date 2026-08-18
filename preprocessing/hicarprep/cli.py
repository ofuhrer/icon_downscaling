"""Command-line entry point for the HICAR meteorological preprocessor."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np

from .boundary import write_boundary_sequence_manifest
from .icon_atmosphere import decode_icon_atmosphere
from .geometry import SleveConfig
from .external import append_epoch
from .pipeline import (
    convert_water_to_hicar_mixing_ratios,
    read_coordinate,
    transform_icon_state,
    write_boundary_condition,
    write_hicar_forcing_record,
)
from .products import (
    assemble_hicar_runtime_domain,
    append_sleve_geometry,
    partition_domain_inputs,
    sha256,
    validate_product_set,
)
from .registry import FieldRegistry
from .remap import (
    RBF_APPLY_BACKENDS,
    RBFWeights,
    VectorRBFWeights,
    build_rbf_weights,
    build_vector_rbf_weights,
)
from .surface import (
    SOIL_WATER_METHODS,
    TEMPERATURE_HEIGHT_METHODS,
    WATER_SNOW_POLICIES,
    prepare_surface_state,
)
from .surface_validation import validate_surface_case


def _datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _registry(path: Path | None) -> FieldRegistry:
    return FieldRegistry.from_json(path) if path else FieldRegistry.default()


def _build_domain(args: argparse.Namespace) -> int:
    registry = _registry(args.registry)
    inventory = partition_domain_inputs(
        args.source,
        static_path=args.static,
        external_path=args.external,
        initial_surface_path=args.initial_surface,
        epoch_valid_from=args.epoch_valid_from,
        initial_valid_time=args.initial_valid_time,
        registry=registry,
    )
    config = SleveConfig(
        nz=args.nz,
        model_top_m=args.model_top_m,
        lowest_layer_m=args.lowest_layer_m,
        stretch_factor=args.stretch_factor,
        decay_rate_large=args.decay_rate_large,
        decay_rate_small=args.decay_rate_small,
        exponent=args.sleve_exponent,
        smooth_window_radius=args.smooth_window_radius,
        smooth_cycles=args.smooth_cycles,
        minimum_jacobian=args.minimum_jacobian,
        minimum_layer_thickness_m=args.minimum_layer_thickness_m,
    )
    append_sleve_geometry(args.static, config=config)
    validate_product_set(args.static, args.external, args.initial_surface, registry)
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0


def _build_weights(args: argparse.Namespace) -> int:
    with netCDF4.Dataset(args.icon_grid) as source:
        source_lat = read_coordinate(source, args.source_lat)
        source_lon = read_coordinate(source, args.source_lon)
    with netCDF4.Dataset(args.static) as target:
        target_lat = np.asarray(target["lat"][:])
        target_lon = np.asarray(target["lon"][:])
    operator = build_rbf_weights(
        source_lat,
        source_lon,
        target_lat,
        target_lon,
        donors=args.donors,
        shape_factor=args.shape_factor,
        maximum_distance_factor=args.maximum_distance_factor,
    )
    operator.write(args.output)
    return 0


def _build_vector_weights(args: argparse.Namespace) -> int:
    with netCDF4.Dataset(args.icon_grid) as source:
        source_lat = read_coordinate(source, args.source_lat)
        source_lon = read_coordinate(source, args.source_lon)
        normal_east = np.asarray(source[args.normal_east][:], dtype=np.float64)
        normal_north = np.asarray(source[args.normal_north][:], dtype=np.float64)
    with netCDF4.Dataset(args.static) as target:
        target_lat = np.asarray(target["lat"][:])
        target_lon = np.asarray(target["lon"][:])
    operator = build_vector_rbf_weights(
        source_lat,
        source_lon,
        normal_east,
        normal_north,
        target_lat,
        target_lon,
        donors=args.donors,
        shape_factor=args.shape_factor,
        maximum_distance_factor=args.maximum_distance_factor,
    )
    operator.write(args.output)
    return 0


def _append_epoch(args: argparse.Namespace) -> int:
    names = append_epoch(
        args.external,
        args.source,
        valid_from=args.valid_from,
        registry=_registry(args.registry),
    )
    print(json.dumps({"appended_epoch_fields": names}, indent=2))
    return 0


def _prepare_surface(args: argparse.Namespace) -> int:
    diagnostics = prepare_surface_state(
        args.icon_surface,
        args.static,
        args.output,
        weights=RBFWeights.read(args.weights),
        noahmp_table=args.noahmp_table,
        soil_water_method=args.soil_water_method,
        water_snow_policy=args.water_snow_policy,
        glacier_landuse_category=args.glacier_landuse_category,
        external_path=args.external,
        allow_static_epoch_back_extrapolation=args.allow_static_epoch_back_extrapolation,
        allow_external_epoch_back_extrapolation=args.allow_external_epoch_back_extrapolation,
        temperature_height_method=args.temperature_height_method,
        climatological_lapse_rate_k_m=args.climatological_lapse_rate_k_m,
        valid_time=args.valid_time,
    )
    print(json.dumps(diagnostics.__dict__, indent=2, sort_keys=True))
    return 0


def _assemble_runtime_domain(args: argparse.Namespace) -> int:
    assemble_hicar_runtime_domain(
        args.static,
        args.surface,
        args.output,
        external_path=args.external,
        valid_time=args.valid_time,
    )
    print(args.output)
    return 0


def _method_path(value: str) -> tuple[str, Path]:
    method, separator, path = value.partition("=")
    if not separator or method not in SOIL_WATER_METHODS or not path:
        raise argparse.ArgumentTypeError(
            "surface product must be METHOD=PATH with METHOD one of "
            + ", ".join(SOIL_WATER_METHODS)
        )
    return method, Path(path)


def _validate_surface_case(args: argparse.Namespace) -> int:
    products = dict(args.product)
    if len(products) != len(args.product):
        raise ValueError("each soil-water method may be supplied only once")
    payload = validate_surface_case(
        args.icon_surface,
        args.static,
        products,
        noahmp_table=args.noahmp_table,
        report_path=args.report,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "PASS_INPUT_PLAUSIBILITY" else 1


def _validate_boundaries(args: argparse.Namespace) -> int:
    payload = write_boundary_sequence_manifest(
        args.boundary,
        args.manifest,
        maximum_interval_seconds=args.maximum_interval_seconds,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _decode_icon_atmosphere(args: argparse.Namespace) -> int:
    payload = decode_icon_atmosphere(
        args.dynamic_grib,
        args.geometry_grib,
        args.icon_extpar,
        args.valid_time,
        args.output,
        missing_qi_policy=args.missing_qi_policy,
        compression_level=args.compression_level,
        range_support_weights=args.range_support_weights,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _prepare_hicar_forcing(args: argparse.Namespace) -> int:
    if args.static_sha256 is not None and (
        len(args.static_sha256) != 64
        or any(character not in "0123456789abcdef" for character in args.static_sha256)
    ):
        raise ValueError("--static-sha256 must be a lowercase SHA-256 digest")
    weights = RBFWeights.read(args.weights)
    vector_weights = VectorRBFWeights.read(args.vector_weights) if args.vector_weights else None
    state, diagnostics = transform_icon_state(
        args.icon_state,
        args.static,
        weights,
        vector_weights=vector_weights,
        column_workers=args.column_workers,
        rbf_backend=args.rbf_backend,
    )
    state = convert_water_to_hicar_mixing_ratios(state)
    lateral_relaxation_authority = (
        "hicarprep sparse_lbc_file_list"
        if args.boundary is not None
        else "HICAR regular forcing relax_filters"
    )
    write_hicar_forcing_record(
        args.output,
        state,
        diagnostics,
        static_path=args.static,
        source_path=args.icon_state,
        target_sst_path=args.target_sst,
        lateral_relaxation_authority=lateral_relaxation_authority,
        static_digest=args.static_sha256,
    )
    if args.boundary is not None:
        with netCDF4.Dataset(args.static) as static:
            x = np.asarray(static["x"][:], dtype=np.float64)
            y = np.asarray(static["y"][:], dtype=np.float64)
        write_boundary_condition(
            args.boundary,
            state,
            x=x,
            y=y,
            boundary_width_m=args.boundary_width_m,
            initial_condition_path=args.output,
            valid_time=str(diagnostics["valid_time"]),
            water_representation="dry-air mixing ratio",
        )
    source_digest = sha256(args.icon_state)
    static_digest = args.static_sha256 or sha256(args.static)
    target_sst_digest = sha256(args.target_sst)
    weights_digest = sha256(args.weights)
    output_digest = sha256(args.output)
    manifest = {
        "schema": "hicarprep-target-forcing-manifest-v1",
        "status": "PASS",
        "valid_time": str(diagnostics["valid_time"]).replace("Z", ""),
        "source": {"path": str(args.icon_state), "sha256": source_digest},
        "static": {"path": str(args.static), "sha256": static_digest},
        "target_sst": {
            "path": str(args.target_sst),
            "sha256": target_sst_digest,
        },
        "weights": {"path": str(args.weights), "sha256": weights_digest},
        "output": {"path": str(args.output), "sha256": output_digest},
        "forcing_file": str(args.output),
        "forcing_sha256": output_digest,
        "diagnostics": diagnostics,
        "water_representation": "dry-air mixing ratio",
        "lateral_relaxation_authority": lateral_relaxation_authority,
    }
    if args.boundary is not None:
        manifest["boundary"] = {
            "path": str(args.boundary),
            "sha256": sha256(args.boundary),
        }
    if args.vector_weights:
        manifest["vector_weights"] = {
            "path": str(args.vector_weights),
            "sha256": sha256(args.vector_weights),
        }
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{args.manifest.name}.", suffix=".partial", dir=args.manifest.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, args.manifest)
        finally:
            temporary.unlink(missing_ok=True)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="hicarprep",
        description="Direct native-grid ICON to target-coordinate HICAR preprocessing",
    )
    commands = result.add_subparsers(dest="command", required=True)

    decode_atmosphere = commands.add_parser(
        "decode-icon-atmosphere",
        help="strictly decode operational native-grid ICON atmospheric GRIB",
    )
    decode_atmosphere.add_argument("--dynamic-grib", type=Path, required=True)
    decode_atmosphere.add_argument("--geometry-grib", type=Path, required=True)
    decode_atmosphere.add_argument("--icon-extpar", type=Path, required=True)
    decode_atmosphere.add_argument("--valid-time", required=True)
    decode_atmosphere.add_argument("--output", type=Path, required=True)
    decode_atmosphere.add_argument(
        "--range-support-weights",
        type=Path,
        help=(
            "validate native W magnitude on the exact scalar-RBF donor support; "
            "all source values must still be finite"
        ),
    )
    decode_atmosphere.add_argument(
        "--missing-qi-policy",
        choices=("error", "source-absent-zero"),
        default="error",
        help="explicit policy for QI, which is absent from operational REA-L",
    )
    decode_atmosphere.add_argument(
        "--compression-level",
        type=int,
        choices=range(10),
        default=1,
        metavar="0..9",
        help="lossless deflate level for the job-local adapter (0 disables compression)",
    )
    decode_atmosphere.set_defaults(func=_decode_icon_atmosphere)

    domain = commands.add_parser(
        "build-domain", help="split field lifetimes and add HICAR geometry"
    )
    domain.add_argument("--source", type=Path, required=True)
    domain.add_argument("--static", type=Path, required=True)
    domain.add_argument("--external", type=Path, required=True)
    domain.add_argument("--initial-surface", type=Path, required=True)
    domain.add_argument("--epoch-valid-from", type=_datetime, required=True)
    domain.add_argument(
        "--initial-valid-time",
        type=_datetime,
        help="valid time of initial-only soil/snow/skin state (defaults to epoch-valid-from)",
    )
    domain.add_argument("--registry", type=Path)
    domain.add_argument("--nz", type=int, default=80)
    domain.add_argument("--model-top-m", type=float, default=12_000.0)
    domain.add_argument("--lowest-layer-m", type=float, default=20.0)
    domain.add_argument("--stretch-factor", type=float, default=0.65)
    domain.add_argument("--decay-rate-large", type=float, default=2.0)
    domain.add_argument("--decay-rate-small", type=float, default=6.0)
    domain.add_argument("--sleve-exponent", type=float, default=1.35)
    domain.add_argument("--smooth-window-radius", type=int, default=5)
    domain.add_argument("--smooth-cycles", type=int, default=10)
    domain.add_argument("--minimum-jacobian", type=float, default=0.0)
    domain.add_argument("--minimum-layer-thickness-m", type=float, default=12.0)
    domain.set_defaults(func=_build_domain)

    weights = commands.add_parser("build-weights", help="cache direct native ICON RBF weights")
    weights.add_argument("--icon-grid", type=Path, required=True)
    weights.add_argument("--static", type=Path, required=True)
    weights.add_argument("--output", type=Path, required=True)
    weights.add_argument("--source-lat", default="clat")
    weights.add_argument("--source-lon", default="clon")
    weights.add_argument("--donors", type=int, default=10)
    weights.add_argument("--shape-factor", type=float, default=1.0)
    weights.add_argument("--maximum-distance-factor", type=float, default=3.0)
    weights.set_defaults(func=_build_weights)

    vector_weights = commands.add_parser(
        "build-vector-weights", help="cache native ICON edge-normal vector RBF weights"
    )
    vector_weights.add_argument("--icon-grid", type=Path, required=True)
    vector_weights.add_argument("--static", type=Path, required=True)
    vector_weights.add_argument("--output", type=Path, required=True)
    vector_weights.add_argument("--source-lat", default="edge_lat")
    vector_weights.add_argument("--source-lon", default="edge_lon")
    vector_weights.add_argument("--normal-east", default="edge_normal_east")
    vector_weights.add_argument("--normal-north", default="edge_normal_north")
    vector_weights.add_argument("--donors", type=int, default=9)
    vector_weights.add_argument("--shape-factor", type=float, default=1.0)
    vector_weights.add_argument("--maximum-distance-factor", type=float, default=3.0)
    vector_weights.set_defaults(func=_build_vector_weights)

    epoch = commands.add_parser(
        "append-epoch", help="append later land-cover/glacier/urban fields for multi-year runs"
    )
    epoch.add_argument("--external", type=Path, required=True)
    epoch.add_argument("--source", type=Path, required=True)
    epoch.add_argument("--valid-from", type=_datetime, required=True)
    epoch.add_argument("--registry", type=Path)
    epoch.set_defaults(func=_append_epoch)

    surface = commands.add_parser(
        "prepare-surface", help="map one native ICON land state to the HICAR target grid"
    )
    surface.add_argument("--icon-surface", type=Path, required=True)
    surface.add_argument("--static", type=Path, required=True)
    surface.add_argument("--weights", type=Path, required=True)
    surface.add_argument("--output", type=Path, required=True)
    surface.add_argument("--noahmp-table", type=Path, required=True)
    surface.add_argument("--soil-water-method", choices=SOIL_WATER_METHODS, default="smi")
    surface.add_argument("--water-snow-policy", choices=WATER_SNOW_POLICIES, default="zero")
    surface.add_argument(
        "--temperature-height-method",
        choices=TEMPERATURE_HEIGHT_METHODS,
        default="int2lm_climatological",
    )
    surface.add_argument("--climatological-lapse-rate-k-m", type=float, default=0.007)
    surface.add_argument("--glacier-landuse-category", type=int, default=24)
    surface.add_argument(
        "--external",
        type=Path,
        help="lifetime-partitioned external product when landuse is not in immutable static",
    )
    surface.add_argument(
        "--allow-static-epoch-back-extrapolation",
        action="store_true",
        help=(
            "research-only: explicitly use a static epoch before its valid-from time; "
            "the exception is recorded in the surface product"
        ),
    )
    surface.add_argument(
        "--allow-external-epoch-back-extrapolation",
        action="store_true",
        help=(
            "research-only: explicitly use the earliest external epoch before its valid-from "
            "time; the exception is recorded in the surface product"
        ),
    )
    surface.add_argument("--valid-time", help="required only when source lacks valid_time")
    surface.set_defaults(func=_prepare_surface)

    runtime_domain = commands.add_parser(
        "assemble-runtime-domain",
        help="merge a prepared surface state into the single domain file HICAR reads",
    )
    runtime_domain.add_argument("--static", type=Path, required=True)
    runtime_domain.add_argument("--surface", type=Path, required=True)
    runtime_domain.add_argument("--output", type=Path, required=True)
    runtime_domain.add_argument(
        "--external", type=Path, help="lifetime-partitioned external parameters to materialize"
    )
    runtime_domain.add_argument(
        "--valid-time", type=_datetime, help="must equal the prepared surface valid time"
    )
    runtime_domain.set_defaults(func=_assemble_runtime_domain)

    surface_validation = commands.add_parser(
        "validate-surface-case",
        help="compare all soil-water transfers for one ICON valid time",
    )
    surface_validation.add_argument("--icon-surface", type=Path, required=True)
    surface_validation.add_argument("--static", type=Path, required=True)
    surface_validation.add_argument("--noahmp-table", type=Path, required=True)
    surface_validation.add_argument(
        "--product",
        action="append",
        type=_method_path,
        required=True,
        help="repeat as smi=PATH, relative_saturation=PATH, and absolute_w_so=PATH",
    )
    surface_validation.add_argument("--report", type=Path, required=True)
    surface_validation.set_defaults(func=_validate_surface_case)

    boundary_sequence = commands.add_parser(
        "validate-boundaries", help="validate and index an ordered LBC time sequence"
    )
    boundary_sequence.add_argument("--boundary", type=Path, action="append", required=True)
    boundary_sequence.add_argument("--manifest", type=Path, required=True)
    boundary_sequence.add_argument("--maximum-interval-seconds", type=float)
    boundary_sequence.set_defaults(func=_validate_boundaries)

    forcing = commands.add_parser(
        "prepare-hicar-forcing",
        help="write one target-grid HICAR forcing/clock record from native ICON",
    )
    forcing.add_argument("--icon-state", type=Path, required=True)
    forcing.add_argument("--static", type=Path, required=True)
    forcing.add_argument("--target-sst", type=Path, required=True)
    forcing.add_argument("--weights", type=Path, required=True)
    forcing.add_argument("--vector-weights", type=Path)
    forcing.add_argument("--output", type=Path, required=True)
    forcing.add_argument(
        "--boundary",
        type=Path,
        help="optional sparse-LBC output for experiments that select sparse relaxation",
    )
    forcing.add_argument("--boundary-width-m", type=float, default=10_000.0)
    forcing.add_argument(
        "--column-workers",
        type=int,
        default=1,
        help=(
            "independent fork workers for vertical column reconstruction; "
            "one preserves the serial path"
        ),
    )
    forcing.add_argument(
        "--rbf-backend",
        choices=RBF_APPLY_BACKENDS,
        default="numpy",
        help="scalar horizontal-remapping implementation",
    )
    forcing.add_argument("--manifest", type=Path)
    forcing.add_argument(
        "--static-sha256",
        help="preverified lowercase static-domain SHA-256 reused by the campaign",
    )
    forcing.set_defaults(func=_prepare_hicar_forcing)

    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
