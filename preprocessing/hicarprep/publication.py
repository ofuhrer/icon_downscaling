"""Fail-closed, receipt-bound validation for publishing closed forcing files."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import netCDF4

from .products import sha256
from .sst import SST_POLICY_VERSION, SST_REMAP_POLICY


_SHA256 = re.compile(r"[0-9a-f]{64}")
_FORCING_VARIABLE_DIMENSIONS = {
    "lat_1": ("y_1", "x_1"),
    "lon_1": ("y_1", "x_1"),
    "P": ("time", "z", "y_1", "x_1"),
    "T": ("time", "z", "y_1", "x_1"),
    "QV": ("time", "z", "y_1", "x_1"),
    "QC": ("time", "z", "y_1", "x_1"),
    "QI": ("time", "z", "y_1", "x_1"),
    "U": ("time", "z", "y_1", "x_1"),
    "V": ("time", "z", "y_1", "x_1"),
    "W": ("time", "z", "y_1", "x_1"),
    "SST": ("time", "y_1", "x_1"),
    "HFL": ("z", "y_1", "x_1"),
    "HHL": ("z_hl", "y_1", "x_1"),
    "HSURF": ("y_1", "x_1"),
    "FR_LAND": ("y_1", "x_1"),
    "SST_unsupported_water_mask": ("y_1", "x_1"),
    "SST_nearest_same_surface_candidate_distance_km": ("y_1", "x_1"),
    "time": ("time",),
}


def _utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _digest(value: Any, label: str) -> str:
    digest = str(value or "").lower()
    if _SHA256.fullmatch(digest) is None:
        raise ValueError(f"publication receipt has invalid {label} SHA-256")
    return digest


def _entry(payload: dict[str, Any], name: str) -> tuple[Path, str]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"publication receipt lacks {name} identity")
    path = value.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError(f"publication receipt lacks {name} path")
    return Path(path), _digest(value.get("sha256"), name)


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def validate_publication_receipt(
    forcing_path: Path,
    static_path: Path,
    receipt_path: Path,
    *,
    expected_valid_time: str | None = None,
    boundary_path: Path | None = None,
    expected_static_sha256: str | None = None,
) -> dict[str, str]:
    """Validate a closed product using the receipt emitted after serialization.

    The file checksum detects arbitrary payload corruption. NetCDF inspection is
    intentionally metadata-only apart from the scalar time coordinate, avoiding
    a second read and decompression of every three-dimensional science field.
    """
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read publication receipt {receipt_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("publication receipt must be a JSON object")
    if payload.get("schema") != "hicarprep-target-forcing-manifest-v1":
        raise ValueError("publication receipt has an unsupported schema")
    if payload.get("status") != "PASS":
        raise ValueError("publication receipt does not record PASS status")

    recorded_forcing, forcing_digest = _entry(payload, "output")
    if not _same_path(recorded_forcing, forcing_path):
        raise ValueError("publication receipt belongs to a different forcing path")
    if not _same_path(Path(str(payload.get("forcing_file", ""))), forcing_path):
        raise ValueError("publication receipt forcing identities are inconsistent")
    if _digest(payload.get("forcing_sha256"), "forcing") != forcing_digest:
        raise ValueError("publication receipt forcing digests are inconsistent")
    if sha256(forcing_path) != forcing_digest:
        raise ValueError("closed forcing file does not match its publication receipt")

    recorded_static, static_digest = _entry(payload, "static")
    if not _same_path(recorded_static, static_path):
        raise ValueError("publication receipt belongs to a different static path")
    if expected_static_sha256 is not None and (
        _digest(expected_static_sha256, "expected static") != static_digest
    ):
        raise ValueError("publication receipt static digest differs from the trusted digest")

    receipt_time = _utc(str(payload.get("valid_time", "")))
    if expected_valid_time is not None and receipt_time != _utc(expected_valid_time):
        raise ValueError("publication receipt valid time differs from the requested time")
    if payload.get("water_representation") != "dry-air mixing ratio":
        raise ValueError("publication receipt has an incompatible water representation")
    if not str(payload.get("lateral_relaxation_authority", "")):
        raise ValueError("publication receipt lacks lateral-relaxation authority")

    boundary_digest = ""
    if boundary_path is not None:
        recorded_boundary, boundary_digest = _entry(payload, "boundary")
        if not _same_path(recorded_boundary, boundary_path):
            raise ValueError("publication receipt belongs to a different boundary path")
        if sha256(boundary_path) != boundary_digest:
            raise ValueError("closed boundary file does not match its publication receipt")
    elif "boundary" in payload:
        raise ValueError("publication receipt includes an unchecked boundary companion")

    try:
        with netCDF4.Dataset(forcing_path) as forcing, netCDF4.Dataset(static_path) as static:
            if str(getattr(forcing, "product_type", "")) != "hicarprep_target_forcing_record":
                raise ValueError("forcing was not produced by hicarprep")
            if str(getattr(forcing, "static_sha256", "")) != static_digest:
                raise ValueError("forcing static identity differs from its publication receipt")
            if str(getattr(forcing, "water_representation", "")) != "dry-air mixing ratio":
                raise ValueError("forcing moisture is not in HICAR dry-air mixing ratios")
            if str(getattr(forcing, "target_w_vertical_coordinate", "")) != (
                "authoritative_static_HFL"
            ):
                raise ValueError("forcing lacks the authoritative target-HFL contract")
            if str(getattr(forcing, "target_w_terrain_wind_basis", "")) != (
                "HICAR_grid_relative"
            ):
                raise ValueError("forcing lacks the grid-relative terrain-wind contract")
            if str(getattr(forcing, "geometry_serialization", "")) != (
                "static_sleve_with_one_ulp_top_cover"
            ):
                raise ValueError("forcing lacks the required geometry serialization contract")
            if (
                str(getattr(forcing, "sst_policy_version", "")) != SST_POLICY_VERSION
                or str(getattr(forcing, "sst_remap_policy", "")) != SST_REMAP_POLICY
            ):
                raise ValueError("forcing lacks the selected SST remapping contract")
            for name, dimensions in _FORCING_VARIABLE_DIMENSIONS.items():
                if name not in forcing.variables:
                    raise ValueError(f"forcing lacks required variable {name}")
                if tuple(forcing[name].dimensions) != dimensions:
                    raise ValueError(f"forcing variable {name} has incompatible dimensions")
            if forcing["time"].size != 1:
                raise ValueError("forcing record must contain exactly one time")
            value = netCDF4.num2date(
                forcing["time"][0],
                forcing["time"].units,
                calendar=getattr(forcing["time"], "calendar", "standard"),
            )
            forcing_time = dt.datetime(
                value.year,
                value.month,
                value.day,
                value.hour,
                value.minute,
                value.second,
                tzinfo=dt.timezone.utc,
            )
            if forcing_time != receipt_time:
                raise ValueError("forcing time differs from its publication receipt")
            if str(getattr(forcing, "valid_time", "")) and (
                _utc(str(forcing.valid_time)) != receipt_time
            ):
                raise ValueError("forcing valid-time attribute differs from its time coordinate")
            dimension_pairs = (("y_1", "y"), ("x_1", "x"), ("z", "level"), ("z_hl", "half_level"))
            for forcing_name, static_name in dimension_pairs:
                if forcing_name not in forcing.dimensions or static_name not in static.dimensions:
                    raise ValueError("forcing/static dimensions are incomplete")
                if forcing.dimensions[forcing_name].size != static.dimensions[static_name].size:
                    raise ValueError("forcing dimensions differ from the runtime domain")
            for name in ("lat", "lon", "HHL", "HFL", "landmask"):
                if name not in static.variables:
                    raise ValueError(f"runtime domain lacks required variable {name}")
    except OSError as exc:
        raise ValueError(f"cannot open publication product: {exc}") from exc

    return {
        "forcing_sha256": forcing_digest,
        "static_sha256": static_digest,
        "boundary_sha256": boundary_digest,
        "valid_time": receipt_time.isoformat().replace("+00:00", "Z"),
    }


def relocate_publication_receipt(
    receipt_path: Path,
    forcing_path: Path,
    published_forcing_path: Path,
    *,
    boundary_path: Path | None = None,
    published_boundary_path: Path | None = None,
) -> None:
    """Atomically retarget an already-validated receipt across atomic renames."""
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    recorded_forcing, _ = _entry(payload, "output")
    if not _same_path(recorded_forcing, forcing_path):
        raise ValueError("cannot relocate a receipt for a different forcing path")
    payload["output"]["path"] = str(published_forcing_path)
    payload["forcing_file"] = str(published_forcing_path)
    if boundary_path is not None or published_boundary_path is not None:
        if boundary_path is None or published_boundary_path is None:
            raise ValueError("boundary receipt relocation requires both old and new paths")
        recorded_boundary, _ = _entry(payload, "boundary")
        if not _same_path(recorded_boundary, boundary_path):
            raise ValueError("cannot relocate a receipt for a different boundary path")
        payload["boundary"]["path"] = str(published_boundary_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{receipt_path.name}.", suffix=".partial", dir=receipt_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt_path)
    finally:
        temporary.unlink(missing_ok=True)
