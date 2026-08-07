#!/usr/bin/env python3
"""Validate and publish the three-hourly REA-L event-reference collection."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4


DIAGNOSTIC_REQUIRED = {
    "sw_direct_down_interval_ref",
    "sw_diffuse_down_interval_ref",
    "lw_down_interval_ref",
    "sw_net_interval_ref",
    "lw_net_interval_ref",
    "latent_heat_flux_interval_ref",
    "sensible_heat_flux_interval_ref",
    "rain_interval_ref",
    "snow_interval_ref",
    "graupel_interval_ref",
    "cloud_area_fraction_ref",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        help="Override plan chunk_root/reference for an additive diagnostic publication",
    )
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    records = plan["records"][::3]
    reference_dir = args.reference_dir or Path(plan["chunk_root"]) / "reference"
    failures: list[str] = []
    publications = []
    required = {
        "psfc_ref",
        "ta2m_ref",
        "td2m_ref",
        "hus2m_ref",
        "u10m_ref",
        "v10m_ref",
        "precipitation_interval_ref",
        "snow_height_ref",
        "swe_ref",
        "source_terrain",
    }
    for record_index, record in enumerate(records):
        valid = datetime.fromisoformat(record["valid_time"])
        stamp = valid.strftime("%Y%m%d_%H%M")
        data = reference_dir / f"rea_l_surface_reference_{stamp}.nc"
        manifest_path = reference_dir / (
            f"rea_l_surface_reference_{stamp}.manifest.json"
        )
        for path in (data, Path(f"{data}.ready"), manifest_path,
                     Path(f"{manifest_path}.ready")):
            if not path.exists():
                failures.append(f"missing {path}")
        if failures and not data.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            digest = sha256(data)
            if manifest.get("status") != "PASS":
                failures.append(f"manifest is not PASS: {manifest_path}")
            if manifest.get("output_sha256") != digest:
                failures.append(f"hash mismatch: {data}")
            expected = record["valid_time"]
            actual = manifest.get("valid_time", "").replace("+00:00", "")
            if actual != expected:
                failures.append(
                    f"valid time mismatch for {data}: {actual} != {expected}"
                )
            with netCDF4.Dataset(data) as dataset:
                missing = required - set(dataset.variables)
                if missing:
                    failures.append(f"{data} missing variables {sorted(missing)}")
                if record_index:
                    missing_diagnostics = DIAGNOSTIC_REQUIRED - set(dataset.variables)
                    if missing_diagnostics:
                        failures.append(
                            f"{data} missing diagnostics {sorted(missing_diagnostics)}"
                        )
                if len(dataset.dimensions["time"]) != 1:
                    failures.append(f"{data} does not have one time record")
            publications.append(
                {
                    "valid_time": expected,
                    "path": str(data.resolve()),
                    "size_bytes": data.stat().st_size,
                    "sha256": digest,
                }
            )
        except Exception as exc:
            failures.append(f"{data}: {exc}")

    expected_count = (len(plan["records"]) - 1) // 3 + 1
    if len(publications) != expected_count:
        failures.append(
            f"validated {len(publications)} records, expected {expected_count}"
        )
    report = {
        "schema_version": 2,
        "status": "FAIL" if failures else "PASS",
        "source_plan": str(args.plan.resolve()),
        "reference_dir": str(reference_dir.resolve()),
        "chunk_id": plan["chunk_id"],
        "start": records[0]["valid_time"],
        "end": records[-1]["valid_time"],
        "interval_hours": 3,
        "expected_records": expected_count,
        "validated_records": len(publications),
        "total_size_bytes": sum(item["size_bytes"] for item in publications),
        "failures": failures,
        "records": publications,
    }
    report_path = reference_dir / "reference_publication.json"
    list_path = reference_dir / "reference_list.txt"
    if report_path.exists() or Path(f"{report_path}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {report_path}")
    if list_path.exists() or Path(f"{list_path}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {list_path}")
    write_atomic(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    if failures:
        raise SystemExit("\n".join(failures))
    write_atomic(
        list_path,
        "".join(f'"{item["path"]}"\n' for item in publications),
    )
    Path(f"{report_path}.ready").touch()
    Path(f"{list_path}.ready").touch()
    print(
        f"PASS: published {len(publications)} REA-L reference records "
        f"({report['total_size_bytes']} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
