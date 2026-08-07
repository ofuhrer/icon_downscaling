#!/usr/bin/env python3
"""Publish compact, checksum-bound evidence for a repeated-day experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_published(path: Path) -> Path:
    marker = Path(f"{path}.ready")
    if not path.is_file() or not marker.is_file():
        raise ValueError(f"publication is incomplete: {path}")
    marker_value = marker.read_text(encoding="utf-8").strip()
    if marker_value and marker_value != sha256(path):
        raise ValueError(f"ready marker does not match publication: {path}")
    return marker


def find_one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            f"expected one {pattern!r} below {directory}, found {len(matches)}"
        )
    return matches[0]


def add_file(
    source: Path,
    relative: Path,
    staging: Path,
    inventory: list[dict[str, Any]],
) -> None:
    if not source.is_file():
        raise ValueError(f"evidence file is missing: {source}")
    destination = staging / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    inventory.append(
        {
            "path": relative.as_posix(),
            "source": str(source.resolve()),
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }
    )


def add_publication(
    source: Path,
    relative: Path,
    staging: Path,
    inventory: list[dict[str, Any]],
) -> None:
    marker = require_published(source)
    add_file(source, relative, staging, inventory)
    add_file(marker, Path(f"{relative}.ready"), staging, inventory)


def publish(
    root: Path,
    target: Path,
    cycle_count: int,
    wind_fix_report: Path,
) -> dict[str, Any]:
    root = root.resolve()
    target = target.resolve()
    if target.exists():
        raise ValueError(f"refusing to replace durable publication: {target}")

    assessment_path = root / "analysis/final/assessment.json"
    assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    if assessment.get("status") != "PASS":
        raise ValueError("final assessment is not PASS")
    if assessment.get("cycles_assessed") != cycle_count:
        raise ValueError("assessment cycle count does not match publication")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent)
    )
    inventory: list[dict[str, Any]] = []
    try:
        add_publication(
            assessment_path,
            Path("analysis/assessment.json"),
            staging,
            inventory,
        )
        add_publication(
            root / "analysis/final/manifest.json",
            Path("analysis/input_manifest.json"),
            staging,
            inventory,
        )
        add_publication(
            root / "submission_plan.json",
            Path("execution/submission_plan.json"),
            staging,
            inventory,
        )
        add_publication(
            root / "execution-runtime-v2/runtime_manifest.json",
            Path("runtime/execution_runtime_manifest.json"),
            staging,
            inventory,
        )
        add_publication(
            root / "analysis-runtime-v1/runtime_manifest.json",
            Path("runtime/analysis_runtime_manifest.json"),
            staging,
            inventory,
        )
        add_file(
            root / "analysis-runtime-v1/repeated_day_summer_windfix_v2.json",
            Path("experiment/repeated_day_summer_windfix_v2.json"),
            staging,
            inventory,
        )
        add_publication(
            wind_fix_report,
            Path("qualification/wind_tendency_fix_qualification.json"),
            staging,
            inventory,
        )

        for cycle in range(1, cycle_count + 1):
            cycle_root = root / "cycles" / f"cycle-{cycle:03d}"
            prefix = Path("cycles") / f"cycle-{cycle:03d}"
            add_publication(
                cycle_root / "run/model_chunk_completion.json",
                prefix / "model_chunk_completion.json",
                staging,
                inventory,
            )
            wind_statistics = find_one(
                cycle_root / "run/wind_climatology", "wind_statistics_*.json"
            )
            add_publication(
                wind_statistics,
                prefix / "wind_statistics.json",
                staging,
                inventory,
            )
            if cycle > 1:
                add_publication(
                    cycle_root / "input/restart_clock_transform.json",
                    prefix / "restart_clock_transform.json",
                    staging,
                    inventory,
                )
                add_publication(
                    cycle_root / "input/restart_input_publication.json",
                    prefix / "restart_input_publication.json",
                    staging,
                    inventory,
                )

        manifest = {
            "schema_version": 1,
            "status": "PASS",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(root),
            "cycle_count": cycle_count,
            "assessment": {
                "decision": assessment["decision"],
                "cycles_assessed": assessment["cycles_assessed"],
                "selected_cycle": assessment["selected_cycle"],
                "equilibration_time": assessment["equilibration_time"],
            },
            "files": sorted(inventory, key=lambda item: item["path"]),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        Path(f"{manifest_path}.ready").write_text(
            sha256(manifest_path) + "\n", encoding="utf-8"
        )
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    published_manifest = target / "manifest.json"
    published = json.loads(published_manifest.read_text(encoding="utf-8"))
    for item in published["files"]:
        path = target / item["path"]
        if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError(f"durable evidence verification failed: {path}")
    if Path(f"{published_manifest}.ready").read_text().strip() != sha256(
        published_manifest
    ):
        raise ValueError("durable manifest ready marker is invalid")
    return published


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--cycle-count", type=int, required=True)
    parser.add_argument("--wind-fix-report", type=Path, required=True)
    args = parser.parse_args()
    manifest = publish(
        args.root,
        args.target,
        args.cycle_count,
        args.wind_fix_report,
    )
    print(
        "PASS: durable repeated-day evidence "
        f"decision={manifest['assessment']['decision']} "
        f"files={len(manifest['files'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
