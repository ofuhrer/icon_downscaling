#!/usr/bin/env python3
"""Audit baseline/candidate snow-ice classes against the official SGI2016 outlines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile

import netCDF4
import numpy as np
from pyproj import CRS, Transformer


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def polygon_records(path: Path):
    with path.open("rb") as stream:
        header = stream.read(100)
        if len(header) != 100 or struct.unpack(">i", header[:4])[0] != 9994:
            raise ValueError("invalid shapefile header")
        while True:
            record_header = stream.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise ValueError("truncated shapefile record header")
            _, words = struct.unpack(">2i", record_header)
            content = stream.read(words * 2)
            if len(content) != words * 2:
                raise ValueError("truncated shapefile record")
            shape_type = struct.unpack("<i", content[:4])[0]
            if shape_type == 0:
                continue
            if shape_type not in (5, 15, 25):
                raise ValueError(f"unsupported shape type: {shape_type}")
            part_count, point_count = struct.unpack("<2i", content[36:44])
            starts = list(struct.unpack(f"<{part_count}i", content[44:44 + 4 * part_count]))
            point_offset = 44 + 4 * part_count
            points = np.frombuffer(content, dtype="<f8", count=point_count * 2, offset=point_offset).reshape(-1, 2).copy()
            starts.append(point_count)
            yield [points[starts[index]:starts[index + 1]] for index in range(part_count)]


def inside_ring(xmesh: np.ndarray, ymesh: np.ndarray, ring: np.ndarray) -> np.ndarray:
    inside = np.zeros(xmesh.shape, dtype=bool)
    previous = ring[-1]
    for current in ring:
        x0, y0 = previous
        x1, y1 = current
        crossing = (y0 > ymesh) != (y1 > ymesh)
        x_intersection = (x1 - x0) * (ymesh - y0) / ((y1 - y0) + 1.0e-300) + x0
        inside ^= crossing & (xmesh < x_intersection)
        previous = current
    return inside


def rasterize(shapefile: Path, transformer: Transformer, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, dict]:
    mask = np.zeros((len(y), len(x)), dtype=bool)
    polygon_count = 0
    ring_count = 0
    transformed_area = 0.0
    for rings in polygon_records(shapefile):
        polygon_count += 1
        transformed = []
        signed_area = 0.0
        for ring in rings:
            tx, ty = transformer.transform(ring[:, 0], ring[:, 1])
            converted = np.column_stack((tx, ty))
            transformed.append(converted)
            signed_area += 0.5 * float(np.sum(tx * np.roll(ty, -1) - np.roll(tx, -1) * ty))
        transformed_area += abs(signed_area)
        xmin = min(float(np.min(ring[:, 0])) for ring in transformed)
        xmax = max(float(np.max(ring[:, 0])) for ring in transformed)
        ymin = min(float(np.min(ring[:, 1])) for ring in transformed)
        ymax = max(float(np.max(ring[:, 1])) for ring in transformed)
        xi = np.flatnonzero((x >= xmin) & (x <= xmax))
        yi = np.flatnonzero((y >= ymin) & (y <= ymax))
        if not len(xi) or not len(yi):
            continue
        xmesh, ymesh = np.meshgrid(x[xi], y[yi])
        polygon = np.zeros(xmesh.shape, dtype=bool)
        for ring in transformed:
            ring_count += 1
            polygon ^= inside_ring(xmesh, ymesh, ring)
        mask[np.ix_(yi, xi)] |= polygon
    return mask, {
        "polygon_records": polygon_count,
        "rings": ring_count,
        "transformed_polygon_area_km2": transformed_area / 1.0e6,
        "cell_center_area_km2": float(mask.sum()) * 0.04,
    }


def static_summary(path: Path, glacier_mask: np.ndarray, elevation_edges: list[float]) -> dict:
    with netCDF4.Dataset(path) as dataset:
        landuse = np.asarray(dataset.variables["landuse"][:], dtype=np.int16)
        topo = np.asarray(dataset.variables["topo"][:], dtype=np.float64)
    if landuse.shape != glacier_mask.shape:
        raise ValueError(f"static grid shape mismatch: {path}")
    glacier_classes, counts = np.unique(landuse[glacier_mask], return_counts=True)
    class_counts = {str(int(key)): int(value) for key, value in zip(glacier_classes, counts)}
    total = int(glacier_mask.sum())
    ice = int(np.count_nonzero(glacier_mask & (landuse == 24)))
    bands = []
    for lower, upper in zip(elevation_edges[:-1], elevation_edges[1:]):
        selection = glacier_mask & (topo >= lower) & (topo < upper)
        count = int(selection.sum())
        matched = int(np.count_nonzero(selection & (landuse == 24)))
        bands.append({
            "lower_m": lower,
            "upper_m": upper if np.isfinite(upper) else None,
            "glacier_cells": count,
            "snow_ice_cells": matched,
            "snow_ice_recall": matched / count if count else None,
        })
    return {
        "path": str(path),
        "sha256": digest(path),
        "glacier_cells": total,
        "snow_ice_cells_inside_glamos": ice,
        "snow_ice_recall": ice / total if total else None,
        "landuse_class_counts_inside_glamos": class_counts,
        "elevation_bands": bands,
    }


def atomic_json(path: Path, payload: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        Path(f"{path}.ready").write_text(f"sha256 {digest(path)}  {path.name}\n", encoding="utf-8")
    finally:
        Path(temporary).unlink(missing_ok=True)


def audit(source_zip: Path, baseline: Path, candidate: Path, output: Path, source_url: str) -> dict:
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite report: {output}")
    with zipfile.ZipFile(source_zip) as archive:
        shapefiles = [
            name for name in archive.namelist()
            if name.lower().endswith("_glaciers.shp")
        ]
        projections = [
            name for name in archive.namelist()
            if name.lower().endswith("_glaciers.prj")
        ]
        if len(shapefiles) != 1 or len(projections) != 1:
            raise ValueError(f"expected one shapefile and projection, got {shapefiles}, {projections}")
        with tempfile.TemporaryDirectory() as directory:
            shp = Path(archive.extract(shapefiles[0], directory))
            prj = archive.read(projections[0]).decode("utf-8", errors="strict")
            with netCDF4.Dataset(candidate) as dataset:
                x = np.asarray(dataset.variables["x"][:], dtype=np.float64)
                y = np.asarray(dataset.variables["y"][:], dtype=np.float64)
                target_crs = CRS.from_user_input(dataset.hicar_projection)
            transformer = Transformer.from_crs(CRS.from_wkt(prj), target_crs, always_xy=True)
            glacier_mask, geometry = rasterize(shp, transformer, x, y)
    if not glacier_mask.any():
        raise ValueError("GLAMOS rasterization produced an empty mask")
    edges = [0.0, 2000.0, 2500.0, 3000.0, 3500.0, float("inf")]
    baseline_report = static_summary(baseline, glacier_mask, edges)
    candidate_report = static_summary(candidate, glacier_mask, edges)
    improvement = candidate_report["snow_ice_recall"] - baseline_report["snow_ice_recall"]
    report = {
        "schema": "hicar-glamos-static-audit/v1",
        "source": {
            "dataset": "Swiss Glacier Inventory 2016, revision 2020",
            "url": source_url,
            "sha256": digest(source_zip),
            "size_bytes": source_zip.stat().st_size,
            "license": "CC BY 4.0",
            "reference_years": "2013-2018",
            "zip_members": sorted(zipfile.ZipFile(source_zip).namelist()),
        },
        "geometry": geometry,
        "baseline_A": baseline_report,
        "candidate_B_C": candidate_report,
        "candidate_minus_baseline_recall": improvement,
        "decision": (
            "GLAMOS_AUDIT_PASS_NO_AUTOMATIC_CORRECTION"
            if candidate_report["snow_ice_recall"] >= 0.8
            else "GLAMOS_AUDIT_ACTION_REQUIRED_BEFORE_GLACIER_CORRECTION"
        ),
        "interpretation_limits": [
            "WorldCover class 24 combines permanent snow and ice, while SGI2016 contains glacier outlines.",
            "SGI2016 refers to 2013-2018 and WorldCover to 2021; glacier retreat creates real epoch disagreement.",
            "Cell-center rasterization at 200 m cannot diagnose narrow glacier tongues or sub-cell fraction.",
            "Debris-covered glacier may correctly map to bare land spectrally but needs an explicit Noah-MP ice decision.",
        ],
        "promotion_limit": "This audit diagnoses disagreement; it does not authorize overwriting land cover or ice state.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = audit(
            args.source_zip.resolve(), args.baseline.resolve(), args.candidate.resolve(),
            args.output.resolve(), args.source_url,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"decision": report["decision"], "candidate_recall": report["candidate_B_C"]["snow_ice_recall"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
