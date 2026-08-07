#!/usr/bin/env python3
"""Run the checksum-bound static-only A/B/C qualification audit.

A is the current published static. B and C intentionally share the improved
NetCDF file: B selects its surface soil class with ``nmp_opt_soil=1`` while C
selects all four ``soil_type_layer`` fields with ``nmp_opt_soil=2``. This tool
audits the static data and records that configuration distinction explicitly;
it does not claim to qualify the coupled response of C.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import netCDF4
import numpy as np
from scipy import ndimage


USGS_WATER = 16
USGS_ICE = 24
EXPECTED_SOIL_BOUNDS_CM = np.array(((0, 10), (10, 30), (30, 70), (70, 150)))
FEATURE_CATEGORIES = {
    "urban": (1,),
    "forest": (11, 12, 13, 14, 15),
    "water": (USGS_WATER,),
    "bare_sparse": (19,),
    "snow_ice": (USGS_ICE,),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publication(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"missing static file: {path}")
    marker = Path(f"{path}.ready")
    if not marker.is_file():
        raise ValueError(f"static file is not published (missing {marker})")
    text = marker.read_text(encoding="utf-8").strip()
    digest = sha256(path)
    if text and digest not in text:
        raise ValueError(f"publication marker does not bind the static checksum: {marker}")
    return {
        "ready_marker": str(marker.resolve()),
        "marker_binds_sha256": bool(text),
        "legacy_empty_marker": not bool(text),
        "sha256": digest,
    }


def published(path: Path) -> None:
    """Backward-compatible assertion used by older callers and tests."""
    publication(path)


def counts(values: np.ndarray, mask: np.ndarray | None = None) -> dict[str, int]:
    selected = values if mask is None else values[mask]
    categories, totals = np.unique(selected.astype(np.int64), return_counts=True)
    return {str(int(category)): int(total) for category, total in zip(categories, totals)}


def fractions(category_counts: dict[str, int]) -> dict[str, float]:
    total = sum(category_counts.values())
    return {key: value / total for key, value in category_counts.items()} if total else {}


def transition(left: np.ndarray, right: np.ndarray) -> dict[str, int]:
    pairs, totals = np.unique(
        np.column_stack((left.ravel().astype(np.int64), right.ravel().astype(np.int64))),
        axis=0,
        return_counts=True,
    )
    return {f"{int(old)}->{int(new)}": int(total) for (old, new), total in zip(pairs, totals)}


def numeric_summary(values: np.ndarray) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    finite = array[np.isfinite(array)]
    if finite.size != array.size:
        raise ValueError("numeric diagnostic contains non-finite values")
    quantiles = np.percentile(finite, (1, 50, 99))
    return {
        "minimum": float(finite.min()),
        "p01": float(quantiles[0]),
        "mean": float(finite.mean()),
        "p50": float(quantiles[1]),
        "p99": float(quantiles[2]),
        "maximum": float(finite.max()),
    }


def component_report(landuse: np.ndarray) -> dict[str, dict[str, int | float]]:
    structure = np.ones((3, 3), dtype=np.uint8)
    output: dict[str, dict[str, int | float]] = {}
    for name, categories in FEATURE_CATEGORIES.items():
        mask = np.isin(landuse, categories)
        labels, number = ndimage.label(mask, structure=structure)
        sizes = np.bincount(labels.ravel())[1:]
        output[name] = {
            "categories": list(categories),
            "cells": int(mask.sum()),
            "fraction": float(mask.mean()),
            "components_8_connected": int(number),
            "largest_component_cells": int(sizes.max()) if sizes.size else 0,
            "median_component_cells": float(np.median(sizes)) if sizes.size else 0.0,
        }
    return output


def terrain_report(fields: dict[str, np.ndarray]) -> dict:
    topo = np.asarray(fields["topo"], dtype=np.float32)
    x = np.asarray(fields["x"], dtype=np.float64)
    y = np.asarray(fields["y"], dtype=np.float64)
    dx = float(np.median(np.diff(x)))
    dy = float(np.median(np.diff(y)))
    if dx <= 0 or dy <= 0:
        raise ValueError("x/y coordinates must increase monotonically")
    dzdy, dzdx = np.gradient(topo, dy, dx)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    d2zdx2 = np.gradient(dzdx, dx, axis=1)
    d2zdy2 = np.gradient(dzdy, dy, axis=0)
    curvature = d2zdx2 + d2zdy2
    report = {
        "elevation_m": numeric_summary(topo),
        "slope_degrees": numeric_summary(np.rad2deg(slope)),
        "laplacian_curvature_per_m": numeric_summary(curvature),
        "grid_spacing_m": {"dx": dx, "dy": dy},
    }
    if {"topo_driving", "topo_highres", "topo_blend_weight"}.issubset(fields):
        weight = np.asarray(fields["topo_blend_weight"])
        mismatch = np.asarray(fields["topo_highres"]) - np.asarray(fields["topo_driving"])
        boundary = weight <= 0.05
        report["boundary_highres_minus_driving_m"] = {
            "cells_weight_le_0.05": int(boundary.sum()),
            "summary": numeric_summary(mismatch[boundary]),
        }
        report["topography_blend_weight"] = numeric_summary(weight)
    return report


def verify_source_identities(cache_dir: Path, identities: list[dict]) -> list[dict]:
    cache_root = cache_dir.resolve()
    verified = []
    for identity in identities:
        relative = Path(str(identity["cache_path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"public source cache_path is not a safe relative path: {relative}")
        source = (cache_root / relative).resolve()
        if cache_root not in source.parents:
            raise ValueError(f"public source escapes source cache: {relative}")
        if not source.is_file():
            raise ValueError(f"public source is missing from checksum cache: {source}")
        size = source.stat().st_size
        if size != int(identity["size_bytes"]):
            raise ValueError(f"public source size mismatch: {source}")
        digest = sha256(source)
        if digest.lower() != str(identity["sha256"]).lower():
            raise ValueError(f"public source checksum mismatch: {source}")
        verified.append({
            "cache_path": str(relative),
            "size_bytes": size,
            "sha256": digest,
            "url": identity["url"],
        })
    return verified


def read_candidate(path: Path) -> tuple[dict, dict[str, np.ndarray]]:
    required = {
        "x", "y", "lat", "lon", "topo", "landmask", "landuse", "soil_type",
        "soil_type_layer", "soil_layer_bounds_cm", "soil_sand_percent",
        "soil_silt_percent", "soil_clay_percent",
    }
    optional = {"topo_highres", "topo_driving", "topo_blend_weight"}
    with netCDF4.Dataset(path) as dataset:
        missing = sorted(required - set(dataset.variables))
        if missing:
            raise ValueError(f"candidate lacks required variables: {missing}")
        names = required | (optional & set(dataset.variables))
        fields = {name: np.asarray(dataset[name][:]) for name in names}
        attrs = {name: dataset.getncattr(name) for name in dataset.ncattrs()}

    shape = fields["topo"].shape
    for name in ("lat", "lon", "landmask", "landuse", "soil_type"):
        if fields[name].shape != shape:
            raise ValueError(f"{name} shape {fields[name].shape} differs from terrain {shape}")
    for name in ("soil_type_layer", "soil_sand_percent", "soil_silt_percent", "soil_clay_percent"):
        if fields[name].shape != (4, *shape):
            raise ValueError(f"{name} must have shape {(4, *shape)}, got {fields[name].shape}")
    if not np.array_equal(fields["soil_layer_bounds_cm"], EXPECTED_SOIL_BOUNDS_CM):
        raise ValueError("soil layer bounds are not 0-10/10-30/30-70/70-150 cm")
    for name in ("lat", "lon", "topo", "soil_sand_percent", "soil_silt_percent", "soil_clay_percent"):
        if not np.all(np.isfinite(fields[name])):
            raise ValueError(f"{name} contains non-finite values")

    landuse = fields["landuse"].astype(np.int64)
    landmask = fields["landmask"].astype(np.int64)
    if np.any((landuse < 1) | (landuse > 24)):
        raise ValueError("landuse contains values outside USGS 1..24")
    if not np.array_equal(landmask == 0, landuse == USGS_WATER):
        raise ValueError("landmask water cells do not exactly match USGS category 16")
    soil_layers = fields["soil_type_layer"].astype(np.int64)
    if np.any((soil_layers < 1) | (soil_layers > 13)):
        raise ValueError("soil_type_layer contains values outside Noah/USDA 1..13")
    if not np.array_equal(fields["soil_type"].astype(np.int64), soil_layers[0]):
        raise ValueError("legacy soil_type is not identical to the 0-10 cm soil layer")

    closure = fields["soil_sand_percent"] + fields["soil_silt_percent"] + fields["soil_clay_percent"]
    if np.any((closure < 95.0) | (closure > 105.0)):
        raise ValueError("soil sand+silt+clay closure is outside 95..105 percent")
    if attrs.get("hicar_static_quality") != "public_source_research_v1":
        raise ValueError("candidate is not marked hicar_static_quality=public_source_research_v1")
    if "public_source_manifest" not in attrs:
        raise ValueError("candidate lacks public_source_manifest")
    if "public_source_identities" not in attrs:
        raise ValueError("candidate lacks checksum-bound public_source_identities")
    if "static_generation_identity" not in attrs:
        raise ValueError("candidate lacks static_generation_identity")
    source_manifest = json.loads(attrs["public_source_manifest"])
    source_identities = json.loads(attrs["public_source_identities"])
    generation_identity = json.loads(attrs["static_generation_identity"])
    if not source_identities or any(
        not {"url", "cache_path", "size_bytes", "sha256"}.issubset(identity)
        for identity in source_identities
    ):
        raise ValueError("public_source_identities is empty or incomplete")
    for name, pattern in (
        ("generator_script_sha256", r"[0-9a-f]{64}"),
        ("runtime_manifest_sha256", r"[0-9a-f]{64}"),
        ("coordinator_commit", r"[0-9a-f]{40}"),
    ):
        if not re.fullmatch(pattern, str(generation_identity.get(name, ""))):
            raise ValueError(f"static_generation_identity has invalid {name}")

    active_soil = (landmask != 0) & (landuse != USGS_ICE)
    landuse_counts = counts(landuse)
    report = {
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "grid": {"ny": shape[0], "nx": shape[1]},
        "landuse_counts": landuse_counts,
        "landuse_fractions": fractions(landuse_counts),
        "connected_components": component_report(landuse),
        "soil_type_counts_by_layer_active_land": [counts(layer, active_soil) for layer in soil_layers],
        "soil_invalid_cells_active_land": int(np.count_nonzero(((soil_layers < 1) | (soil_layers > 13))[:, active_soil])),
        "soil_composition_closure_percent": numeric_summary(closure),
        "terrain": terrain_report(fields),
        "public_source_manifest": source_manifest,
        "public_source_identities": source_identities,
        "static_generation_identity": generation_identity,
    }
    return report, fields


def read_baseline(path: Path, candidate_fields: dict[str, np.ndarray]) -> tuple[dict, dict[str, np.ndarray]]:
    optional = ("topo_highres", "topo_driving", "topo_blend_weight")
    with netCDF4.Dataset(path) as dataset:
        required = ("x", "y", "lat", "lon", "topo", "landmask", "landuse", "soil_type")
        missing = [name for name in required if name not in dataset.variables]
        if missing:
            raise ValueError(f"baseline lacks required variables: {missing}")
        names = required + tuple(name for name in optional if name in dataset.variables)
        fields = {name: np.asarray(dataset[name][:]) for name in names}
        attrs = {name: dataset.getncattr(name) for name in dataset.ncattrs()}
    for name in ("x", "y"):
        if not np.array_equal(fields[name], candidate_fields[name]):
            raise ValueError(f"baseline {name} coordinates differ from candidate")
    coordinate_differences = {}
    for name in ("lat", "lon"):
        if fields[name].shape != candidate_fields[name].shape:
            raise ValueError(f"baseline {name} grid differs from candidate")
        difference = np.asarray(candidate_fields[name], dtype=np.float64) - np.asarray(fields[name], dtype=np.float64)
        coordinate_differences[name] = numeric_summary(difference)
        if not np.allclose(fields[name], candidate_fields[name], atol=1e-6, rtol=0.0):
            raise ValueError(f"baseline {name} coordinates differ from candidate")
    landuse_counts = counts(fields["landuse"])
    topo_difference = np.asarray(candidate_fields["topo"]) - np.asarray(fields["topo"])
    report = {
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "attrs": {
            key: attrs[key]
            for key in ("hicar_static_quality", "public_sources", "soil_initialization")
            if key in attrs
        },
        "landuse_counts": landuse_counts,
        "landuse_fractions": fractions(landuse_counts),
        "connected_components": component_report(fields["landuse"]),
        "terrain": terrain_report(fields),
        "coordinate_differences_candidate_minus_baseline": coordinate_differences,
        "landuse_transition": transition(fields["landuse"], candidate_fields["landuse"]),
        "landuse_changed_cells": int(np.count_nonzero(fields["landuse"] != candidate_fields["landuse"])),
        "surface_soil_transition": transition(fields["soil_type"], candidate_fields["soil_type"]),
        "surface_soil_changed_cells": int(np.count_nonzero(fields["soil_type"] != candidate_fields["soil_type"])),
        "topography_difference_m": numeric_summary(topo_difference),
        "topography_max_abs_difference_m": float(np.max(np.abs(topo_difference))),
    }
    return report, fields


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = Path(f"{path}.ready")
    marker.unlink(missing_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        digest = sha256(path)
        marker_tmp = Path(f"{marker}.tmp")
        marker_tmp.write_text(f"sha256 {digest}  {path.name}\n", encoding="utf-8")
        os.replace(marker_tmp, marker)
    finally:
        Path(temporary).unlink(missing_ok=True)


def build_audit(baseline: Path, candidate: Path, source_cache: Path, topo_tolerance_m: float) -> dict:
    baseline_publication = publication(baseline)
    candidate_publication = publication(candidate)
    candidate_report, candidate_fields = read_candidate(candidate)
    baseline_report, _ = read_baseline(baseline, candidate_fields)
    verified_sources = verify_source_identities(source_cache, candidate_report["public_source_identities"])
    terrain_unchanged = baseline_report["topography_max_abs_difference_m"] <= topo_tolerance_m
    preserved_identity = (
        candidate_report["public_source_manifest"].get("copernicus_dem", {}).get("identity")
    )
    terrain_identity_bound = bool(
        preserved_identity
        and preserved_identity.get("sha256") == baseline_publication["sha256"]
        and set(preserved_identity.get("variables", ()))
        == {"topo", "topo_highres", "topo_driving", "topo_blend_weight"}
    )
    gates = {
        "baseline_published": {"passed": True, **baseline_publication},
        "candidate_published": {"passed": True, **candidate_publication},
        "candidate_schema_categories_soil_closure": {"passed": True},
        "source_files_size_and_sha256": {"passed": True, "verified_files": len(verified_sources)},
        "identical_grid_coordinates": {"passed": True},
        "p0_terrain_unchanged": {
            "passed": terrain_unchanged,
            "tolerance_m": topo_tolerance_m,
            "maximum_absolute_difference_m": baseline_report["topography_max_abs_difference_m"],
        },
        "baseline_terrain_checksum_and_variables_bound": {
            "passed": terrain_identity_bound,
            "preserved_topography_identity": preserved_identity,
        },
        "atomic_audit_publication": {"passed": True, "verified_after_write": False},
    }
    passed = all(gate["passed"] for gate in gates.values())
    return {
        "schema": "hicar-static-abc-audit/v2",
        "scope": "static_only_no_coupled_simulation",
        "arms": {
            "A": {
                "static": {"path": str(baseline.resolve()), "sha256": baseline_publication["sha256"]},
                "land_surface_configuration": {"nmp_opt_soil": 1, "soiltexture_var": ""},
                "description": "current published static and dominant surface soil class",
            },
            "B": {
                "static": {"path": str(candidate.resolve()), "sha256": candidate_publication["sha256"]},
                "land_surface_configuration": {"nmp_opt_soil": 1, "soiltexture_var": ""},
                "description": "modal WorldCover and improved 0-10 cm surface soil class",
            },
            "C": {
                "static": {"path": str(candidate.resolve()), "sha256": candidate_publication["sha256"]},
                "land_surface_configuration": {"nmp_opt_soil": 2, "soiltexture_var": "soil_type_layer"},
                "description": "same static bytes as B with four depth-varying soil classes selected at runtime",
            },
        },
        "invariant": {
            "B_and_C_share_identical_static_bytes": True,
            "B_and_C_are_not_physically_equivalent_coupled_configurations": True,
        },
        "baseline_A": baseline_report,
        "candidate_B_C": candidate_report,
        "verified_source_files": verified_sources,
        "gates": gates,
        "decision": (
            "STATIC_AUDIT_PASS_PAIRED_COUPLED_CASES_REQUIRED"
            if passed else "REJECT_STATIC_CANDIDATE"
        ),
        "promotion_limit": (
            "Passing this audit permits bounded paired process cases only; it does not promote B or C "
            "to national production and does not validate nmp_opt_soil=2 physics."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--source-cache", required=True, type=Path)
    parser.add_argument("--topography-tolerance-m", type=float, default=1.0e-4)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.topography_tolerance_m < 0:
        raise SystemExit("--topography-tolerance-m must be non-negative")
    try:
        payload = build_audit(
            args.baseline, args.candidate, args.source_cache, args.topography_tolerance_m
        )
        atomic_json(args.output, payload)
        publication(args.output)
        # Republish once with the post-write verification represented inside
        # the checksum-bound payload itself.
        payload["gates"]["atomic_audit_publication"]["verified_after_write"] = True
        atomic_json(args.output, payload)
        publication(args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
