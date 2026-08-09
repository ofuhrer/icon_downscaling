#!/usr/bin/env python3
"""Replace legacy midpoint HFL with the authoritative static SLEVE geometry.

This is a narrow migration for hicarprep records written before the forcing
pipeline preserved the static mass-level coordinate.  Atmospheric values are
copied bit-for-bit; only HHL/HFL, the paired sparse-boundary geometry, and the
forcing hash stored by that boundary are changed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import netCDF4
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from preprocessing.hicarprep.products import sha256  # noqa: E402
from preprocessing.hicarprep.pipeline import (  # noqa: E402
    forcing_geometry_for_serialization,
)


def _temporary_copy(path: Path) -> Path:
    source_before = path.stat()
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    # Avoid shutil.copyfile's Linux sendfile fast path.  On the Balfrin
    # parallel filesystem it reproducibly produced a same-sized but
    # checksum-different copy for one 1.46 GB sparse-LBC record, while a
    # buffered read/write copy was byte-identical and the source stayed
    # stable.  Keep the checksum check as the publication boundary.
    with path.open("rb") as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, length=16 * 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    source_after = path.stat()
    source_unchanged = (
        source_before.st_dev == source_after.st_dev
        and source_before.st_ino == source_after.st_ino
        and source_before.st_size == source_after.st_size
        and source_before.st_mtime_ns == source_after.st_mtime_ns
    )
    if (
        not source_unchanged
        or temporary.stat().st_size != source_after.st_size
        or sha256(temporary) != sha256(path)
    ):
        temporary.unlink(missing_ok=True)
        raise OSError(f"temporary copy of {path} is not byte-identical")
    return temporary


def repair_pair(
    forcing_path: Path,
    boundary_path: Path,
    static_path: Path,
    *,
    validator: Path,
) -> tuple[str, str]:
    forcing_ready = Path(f"{forcing_path}.ready")
    boundary_ready = Path(f"{boundary_path}.ready")
    static_sha = sha256(static_path)
    with netCDF4.Dataset(forcing_path) as forcing, netCDF4.Dataset(
        boundary_path
    ) as boundary:
        migrated = (
            str(getattr(forcing, "geometry_source_sha256", "")) == static_sha
            and str(getattr(boundary, "geometry_source_sha256", "")) == static_sha
            and str(getattr(forcing, "geometry_serialization", ""))
            == "static_sleve_with_one_ulp_top_cover"
        )
    if migrated:
        if forcing_ready.exists() != boundary_ready.exists():
            raise FileNotFoundError(
                "forcing and boundary publication markers are inconsistent"
            )
        subprocess.run(
            [
                sys.executable,
                str(validator),
                "--forcing-file",
                str(forcing_path),
                "--boundary-file",
                str(boundary_path),
                "--static-file",
                str(static_path),
            ],
            check=True,
        )
        forcing_sha = sha256(forcing_path)
        boundary_sha = sha256(boundary_path)
        # A published migrated pair is immutable.  Revalidation must not make
        # a valid concurrent reader observe a transiently incomplete product.
        # Restore markers only for a previously unpublished pair.
        if not forcing_ready.exists():
            forcing_ready.touch(exist_ok=False)
            boundary_ready.touch(exist_ok=False)
        return forcing_sha, boundary_sha
    if forcing_ready.exists() != boundary_ready.exists():
        raise FileNotFoundError("forcing and boundary publication markers are inconsistent")

    forcing_temporary: Path | None = None
    boundary_temporary: Path | None = None
    forcing_ready.unlink(missing_ok=True)
    boundary_ready.unlink(missing_ok=True)
    try:
        with netCDF4.Dataset(static_path) as static:
            static_hhl = np.asarray(static["HHL"][:])
            static_hfl = np.asarray(static["HFL"][:])

        forcing_temporary = _temporary_copy(forcing_path)
        with netCDF4.Dataset(forcing_temporary, "r+") as forcing:
            if str(getattr(forcing, "product_type", "")) != "hicarprep_target_forcing_record":
                raise ValueError("forcing is not a hicarprep target record")
            if str(getattr(forcing, "static_sha256", "")) != static_sha:
                raise ValueError("forcing and static domain identities differ")
            serialized_hhl, serialized_hfl = forcing_geometry_for_serialization(
                static_hhl, static_hfl
            )
            forcing["HHL"][:] = serialized_hhl
            forcing["HFL"][:] = serialized_hfl
            forcing.geometry_source = "authoritative static SLEVE HHL/HFL"
            forcing.geometry_source_sha256 = static_sha
            forcing.geometry_serialization = "static_sleve_with_one_ulp_top_cover"
        forcing_sha = sha256(forcing_temporary)

        boundary_temporary = _temporary_copy(boundary_path)
        with netCDF4.Dataset(boundary_temporary, "r+") as boundary:
            if str(getattr(boundary, "product_type", "")) != "hicar_lateral_boundary_state":
                raise ValueError("boundary is not a hicarprep sparse LBC record")
            rows = np.asarray(boundary["row"][:], dtype=np.int64)
            columns = np.asarray(boundary["column"][:], dtype=np.int64)
            boundary["HHL"][:] = static_hhl[:, rows, columns]
            boundary["HFL"][:] = static_hfl[:, rows, columns]
            boundary.initial_condition_sha256 = forcing_sha
            boundary.geometry_source = "authoritative static SLEVE HHL/HFL"
            boundary.geometry_source_sha256 = static_sha

        subprocess.run(
            [
                sys.executable,
                str(validator),
                "--forcing-file",
                str(forcing_temporary),
                "--boundary-file",
                str(boundary_temporary),
                "--static-file",
                str(static_path),
            ],
            check=True,
        )
        boundary_sha = sha256(boundary_temporary)
        os.replace(forcing_temporary, forcing_path)
        forcing_temporary = None
        os.replace(boundary_temporary, boundary_path)
        boundary_temporary = None
        forcing_ready.touch(exist_ok=False)
        boundary_ready.touch(exist_ok=False)
        return forcing_sha, boundary_sha
    finally:
        if forcing_temporary is not None:
            forcing_temporary.unlink(missing_ok=True)
        if boundary_temporary is not None:
            boundary_temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forcing-file", type=Path, required=True)
    parser.add_argument("--boundary-file", type=Path, required=True)
    parser.add_argument("--static-file", type=Path, required=True)
    parser.add_argument(
        "--validator",
        type=Path,
        default=ROOT / "case_studies/swiss_200m/validation/validate_forcing.py",
    )
    args = parser.parse_args()
    forcing_sha, boundary_sha = repair_pair(
        args.forcing_file,
        args.boundary_file,
        args.static_file,
        validator=args.validator,
    )
    print(f"PASS forcing_sha256={forcing_sha} boundary_sha256={boundary_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
