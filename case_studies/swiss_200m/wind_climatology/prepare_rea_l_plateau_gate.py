#!/usr/bin/env python3
"""Prepare a publication-safe REA-L forcing plan for the Plateau case gate."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def require_published(path: Path, label: str) -> None:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def build_plan(source_plan: Path, gate_root: Path, output: Path) -> dict[str, Any]:
    require_published(source_plan, "source forcing plan")
    source = json.loads(source_plan.read_text())
    if source.get("schema_version") != 1 or source.get("record_count") != 25:
        raise ValueError("source plan is not the expected 25-record Plateau plan")
    if source.get("start") != "2014-11-21T00:00:00":
        raise ValueError("source plan has the wrong Plateau start")
    if source.get("end") != "2014-11-22T00:00:00":
        raise ValueError("source plan has the wrong Plateau end")

    forcing_root = gate_root / "forcing"
    records = []
    for record in source["records"]:
        stamp = record["valid_time"].replace("-", "").replace(":", "")
        stamp = stamp.replace("T", "_")
        forcing = forcing_root / f"rea_l_hicar_surface_wind_{stamp}.nc"
        records.append(
            {
                **record,
                "forcing_file": str(forcing),
                "ready_marker": f"{forcing}.ready",
            }
        )
    payload = {
        **source,
        "chunk_id": "plateau-inversion-rea-l-selection-gate-v1",
        "chunk_root": str(gate_root),
        "producer_root": str(gate_root),
        "forcing_list": str(gate_root / "forcing_list.txt"),
        "records": records,
        "status": "PLANNED_SELECTION_GATE",
        "source_plan": str(source_plan),
    }
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to replace existing gate plan: {output}")
    write_json_atomic(output, payload)
    Path(f"{output}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_plan(
        args.source_plan.resolve(),
        args.gate_root.resolve(),
        args.output.resolve(),
    )
    print(f"Plateau REA-L gate plan published: {len(payload['records'])} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
