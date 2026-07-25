#!/usr/bin/env python3
"""Validate HICAR SLEVE geometry directly from a static terrain file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def auto_level_one(
    *,
    nz: int,
    top_height: float,
    lowest_layer: float,
    stretch_factor: float,
) -> np.ndarray:
    x1 = (2.0 * stretch_factor - 1.0) * lowest_layer
    b = (
        top_height - (x1 / 6.0) * nz**3 - (lowest_layer - x1 / 6.0) * nz
    ) / (nz**2 - nz**3 / 3.0 - 2.0 * nz / 3.0)
    a = (x1 - 2.0 * b) / 6.0
    c = lowest_layer - (a + b)
    indices = np.arange(nz + 1, dtype=np.float64)
    interfaces = a * indices**3 + b * indices**2 + c * indices
    thickness = np.diff(interfaces)
    if (
        abs(interfaces[0]) > 1.0e-6
        or abs(interfaces[-1] - top_height) > 0.01 * top_height
        or np.any(thickness <= 0.0)
    ):
        raise ValueError("invalid auto_level=1 vertical distribution")
    return thickness


def smooth_large_scale(
    terrain: np.ndarray,
    *,
    window_radius: int,
    cycles: int,
) -> np.ndarray:
    size = 2 * window_radius + 1
    if window_radius < 0:
        raise ValueError("window radius must be non-negative")
    if cycles < 0:
        raise ValueError("smoothing cycles must be non-negative")
    if not window_radius or not cycles:
        return np.asarray(terrain, dtype=np.float32).copy()

    ny, nx = terrain.shape
    row_count = (
        np.minimum(np.arange(ny) + window_radius + 1, ny)
        - np.maximum(np.arange(ny) - window_radius, 0)
    )
    column_count = (
        np.minimum(np.arange(nx) + window_radius + 1, nx)
        - np.maximum(np.arange(nx) - window_radius, 0)
    )
    counts = row_count[:, None] * column_count[None, :]

    # HICAR truncates each square window at a domain edge. An integral-image
    # box sum reproduces that definition without SciPy, which is absent from
    # the standard Balfrin validation environment.
    smoothed = np.asarray(terrain, dtype=np.float32).copy()
    for _ in range(cycles):
        padded = np.pad(
            smoothed,
            ((window_radius, window_radius), (window_radius, window_radius)),
            mode="constant",
        )
        integral = np.pad(
            padded.astype(np.float64, copy=False),
            ((1, 0), (1, 0)),
            mode="constant",
        ).cumsum(axis=0).cumsum(axis=1)
        sums = (
            integral[size:, size:]
            - integral[:-size, size:]
            - integral[size:, :-size]
            + integral[:-size, :-size]
        )
        smoothed = np.asarray(sums / counts, dtype=np.float32)
    return smoothed


def _sleve_terms(zeta: float, *, smooth_height: float, decay: float, exponent: float) -> tuple[float, float]:
    scale = smooth_height / decay
    denominator = np.sinh((smooth_height / scale) ** exponent)
    coordinate = (zeta / scale) ** exponent
    basis = np.sinh((smooth_height / scale) ** exponent - coordinate) / denominator
    derivative = (
        -exponent
        / scale**exponent
        * zeta ** (exponent - 1.0)
        * np.cosh((smooth_height / scale) ** exponent - coordinate)
        / denominator
    )
    return float(basis), float(derivative)


def validate_geometry(
    static_path: Path,
    *,
    terrain_variable: str,
    nz: int,
    top_height: float,
    lowest_layer: float,
    stretch_factor: float,
    decay_large: float,
    decay_small: float,
    sleve_exponent: float,
    smooth_window_radius: int,
    smooth_cycles: int,
    required_mass_jacobian: float = 0.0,
    required_interface_thickness: float = 0.0,
    required_mass_spacing: float = 0.0,
) -> dict[str, object]:
    with netCDF4.Dataset(static_path) as dataset:
        if terrain_variable not in dataset.variables:
            raise KeyError(f"missing terrain variable {terrain_variable!r}")
        terrain = np.asarray(
            np.ma.asarray(dataset.variables[terrain_variable][:]).filled(np.nan),
            dtype=np.float32,
        )
    if terrain.ndim != 2 or not np.isfinite(terrain).all():
        raise ValueError("terrain must be a finite two-dimensional field")

    dz = auto_level_one(
        nz=nz,
        top_height=top_height,
        lowest_layer=lowest_layer,
        stretch_factor=stretch_factor,
    )
    smooth_height = float(np.sum(dz))
    h1 = smooth_large_scale(
        terrain,
        window_radius=smooth_window_radius,
        cycles=smooth_cycles,
    )
    h2 = terrain - h1

    minimum_jacobian = np.inf
    minimum_jacobian_record: dict[str, object] = {}
    minimum_interface_thickness = np.inf
    minimum_interface_record: dict[str, object] = {}
    minimum_mass_spacing = np.inf
    minimum_mass_record: dict[str, object] = {}
    previous_interface = terrain.astype(np.float64)
    previous_mass: np.ndarray | None = None
    cumulative = 0.0

    for level, layer_thickness in enumerate(dz, start=1):
        mass_zeta = cumulative + 0.5 * layer_thickness
        interface_zeta = cumulative + layer_thickness
        b1_mass, db1_mass = _sleve_terms(
            mass_zeta,
            smooth_height=smooth_height,
            decay=decay_large,
            exponent=sleve_exponent,
        )
        b2_mass, db2_mass = _sleve_terms(
            mass_zeta,
            smooth_height=smooth_height,
            decay=decay_small,
            exponent=sleve_exponent,
        )
        b1_interface, _ = _sleve_terms(
            interface_zeta,
            smooth_height=smooth_height,
            decay=decay_large,
            exponent=sleve_exponent,
        )
        b2_interface, _ = _sleve_terms(
            interface_zeta,
            smooth_height=smooth_height,
            decay=decay_small,
            exponent=sleve_exponent,
        )

        jacobian = 1.0 + h1 * db1_mass + h2 * db2_mass
        mass_height = mass_zeta + h1 * b1_mass + h2 * b2_mass
        interface_height = interface_zeta + h1 * b1_interface + h2 * b2_interface
        interface_thickness = interface_height - previous_interface

        local_index = np.unravel_index(np.argmin(jacobian), jacobian.shape)
        local_value = float(jacobian[local_index])
        if local_value < minimum_jacobian:
            minimum_jacobian = local_value
            minimum_jacobian_record = {
                "value": local_value,
                "level": level,
                "index_yx": [int(local_index[0]), int(local_index[1])],
                "terrain_m": float(terrain[local_index]),
                "h1_m": float(h1[local_index]),
                "h2_m": float(h2[local_index]),
            }

        local_index = np.unravel_index(
            np.argmin(interface_thickness), interface_thickness.shape
        )
        local_value = float(interface_thickness[local_index])
        if local_value < minimum_interface_thickness:
            minimum_interface_thickness = local_value
            minimum_interface_record = {
                "value_m": local_value,
                "level": level,
                "index_yx": [int(local_index[0]), int(local_index[1])],
            }

        if previous_mass is not None:
            mass_spacing = mass_height - previous_mass
            local_index = np.unravel_index(np.argmin(mass_spacing), mass_spacing.shape)
            local_value = float(mass_spacing[local_index])
            if local_value < minimum_mass_spacing:
                minimum_mass_spacing = local_value
                minimum_mass_record = {
                    "value_m": local_value,
                    "between_levels": [level - 1, level],
                    "index_yx": [int(local_index[0]), int(local_index[1])],
                }

        previous_interface = interface_height
        previous_mass = mass_height
        cumulative = interface_zeta

    failures: list[str] = []
    if minimum_jacobian < required_mass_jacobian:
        failures.append(
            f"minimum mass-level Jacobian {minimum_jacobian} is below "
            f"{required_mass_jacobian}"
        )
    if minimum_interface_thickness < required_interface_thickness:
        failures.append(
            f"minimum interface layer thickness {minimum_interface_thickness} m "
            f"{required_interface_thickness} m"
        )
    if minimum_mass_spacing < required_mass_spacing:
        failures.append(
            f"minimum mass-level spacing {minimum_mass_spacing} m is below "
            f"{required_mass_spacing} m"
        )

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "static_path": str(static_path.resolve()),
        "static_sha256": sha256(static_path),
        "terrain_variable": terrain_variable,
        "terrain_shape": list(terrain.shape),
        "terrain_range_m": [float(np.min(terrain)), float(np.max(terrain))],
        "configuration": {
            "auto_level": 1,
            "nz": nz,
            "model_top_height_m": top_height,
            "height_lowest_level_m": lowest_layer,
            "stretch_factor": stretch_factor,
            "decay_rate_large": decay_large,
            "decay_rate_small": decay_small,
            "sleve_exponent": sleve_exponent,
            "terrain_smooth_window_radius": smooth_window_radius,
            "terrain_smooth_cycles": smooth_cycles,
        },
        "acceptance": {
            "minimum_mass_jacobian": required_mass_jacobian,
            "minimum_interface_layer_thickness_m": required_interface_thickness,
            "minimum_mass_level_spacing_m": required_mass_spacing,
        },
        "generated_layer_thickness_range_m": [
            float(np.min(dz)),
            float(np.max(dz)),
        ],
        "minimum_mass_jacobian": minimum_jacobian_record,
        "minimum_interface_layer_thickness": minimum_interface_record,
        "minimum_mass_level_spacing": minimum_mass_record,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("static_file", type=Path)
    parser.add_argument("--terrain-variable", default="topo")
    parser.add_argument("--nz", type=int, default=80)
    parser.add_argument("--model-top-height", type=float, default=12_000.0)
    parser.add_argument("--height-lowest-level", type=float, default=15.0)
    parser.add_argument("--stretch-factor", type=float, default=0.65)
    parser.add_argument("--decay-rate-large", type=float, default=2.0)
    parser.add_argument("--decay-rate-small", type=float, default=6.0)
    parser.add_argument("--sleve-exponent", type=float, default=1.35)
    parser.add_argument("--smooth-window-radius", type=int, default=5)
    parser.add_argument("--smooth-cycles", type=int, default=100)
    parser.add_argument("--minimum-mass-jacobian", type=float, default=0.0)
    parser.add_argument("--minimum-interface-thickness", type=float, default=0.0)
    parser.add_argument("--minimum-mass-spacing", type=float, default=0.0)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = validate_geometry(
        args.static_file,
        terrain_variable=args.terrain_variable,
        nz=args.nz,
        top_height=args.model_top_height,
        lowest_layer=args.height_lowest_level,
        stretch_factor=args.stretch_factor,
        decay_large=args.decay_rate_large,
        decay_small=args.decay_rate_small,
        sleve_exponent=args.sleve_exponent,
        smooth_window_radius=args.smooth_window_radius,
        smooth_cycles=args.smooth_cycles,
        required_mass_jacobian=args.minimum_mass_jacobian,
        required_interface_thickness=args.minimum_interface_thickness,
        required_mass_spacing=args.minimum_mass_spacing,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{args.report.name}.",
            suffix=".tmp",
            dir=args.report.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered + "\n")
        os.replace(temporary, args.report)
    print(rendered)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
