#!/usr/bin/env python3
"""Publish a two-copy REA-L forcing reproducibility probe plan."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


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


def build_plan(
    output: Path, probe_root: Path, valid_time: datetime
) -> dict[str, Any]:
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to replace probe plan: {output}")
    cycle_date = valid_time.strftime("%Y%m%d")
    cycle_time = "0000"
    step_hours = valid_time.hour
    records = []
    for index in range(2):
        records.append(
            {
                "index": index,
                "cycle_date": cycle_date,
                "cycle_time": cycle_time,
                "step_hours": step_hours,
                "valid_time": valid_time.isoformat(),
                "forcing_file": str(
                    probe_root
                    / "forcing"
                    / (
                        f"rea_l_hicar_{valid_time:%Y%m%d_%H%M}"
                        f"_copy{index}.nc"
                    )
                ),
            }
        )
    payload = {
        "schema_version": 1,
        "status": "PLANNED",
        "purpose": "forcing-array-reproducibility-probe",
        "chunk_id": f"forcing-repro-{valid_time:%Y%m%dT%H%M%S}",
        "chunk_root": str(probe_root),
        "producer_root": str(probe_root),
        "valid_time": valid_time.isoformat(),
        "records": records,
    }
    write_json_atomic(output, payload)
    Path(f"{output}.ready").touch()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-root", type=Path, required=True)
    parser.add_argument("--valid-time", required=True)
    args = parser.parse_args()
    payload = build_plan(
        args.output.resolve(),
        args.probe_root.resolve(),
        datetime.fromisoformat(args.valid_time),
    )
    print(f"forcing reproducibility plan: {payload['chunk_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
