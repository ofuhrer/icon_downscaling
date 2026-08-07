#!/usr/bin/env python3
"""Merge unchanged summer controls with new cold-start origin completions."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def published(path: Path) -> dict:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"input is not published: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") not in {"PASS", "PLANNED"}:
        raise ValueError(f"input is not passing: {path}")
    return payload


def merge_payloads(
    baseline_contract: dict,
    baseline_completion: dict,
    candidate_completion: dict,
    static_2: Path,
    static_3: Path,
) -> tuple[dict, dict]:
    baseline_chains = {item["chain_id"]: item for item in baseline_completion["chains"]}
    candidate_chains = {item["chain_id"]: item for item in candidate_completion["chains"]}
    required_baseline = ("reference", "origin-20200701")
    required_candidate = ("native-origin-20200702", "native-origin-20200703")
    for name in required_baseline:
        if name not in baseline_chains:
            raise ValueError(f"baseline completion lacks {name}")
    for name in required_candidate:
        if name not in candidate_chains:
            raise ValueError(f"candidate completion lacks {name}")

    contract = copy.deepcopy(baseline_contract)
    contract.update(
        {
            "experiment": "direct-native soil-type-aware cold-start intervention",
            "phase": "cold-start-intervention",
            "status": "PLANNED",
        }
    )
    contract["execution"]["origin_specific_rea_l_land_snow_initialization"] = True
    contract["execution"]["cold_start_intervention"] = (
        "direct native ICON interpolation plus TERRA SMI to NoahMP STAS VWC"
    )
    contract["decision_rule"]["method_pass"] = (
        "both direct-native reset origins pass the established retained-day "
        "wind/PBL and slow-state thresholds against the unchanged reference"
    )
    contract["decision_rule"]["failure"] = (
        "retain direct native interpolation but reject or bound the SMI transform; "
        "do not promote this cold start"
    )
    replacements = (
        (1, "native-origin-20200702", static_2),
        (2, "native-origin-20200703", static_3),
    )
    for index, chain_id, static_file in replacements:
        contract["windows"][index]["chain_id"] = chain_id
        contract["windows"][index]["static_file"] = str(static_file.resolve())

    completion = {
        "schema_version": 1,
        "status": "PASS",
        "campaign_id": "native-land-cold-start-july-v1-with-unchanged-controls",
        "chains": [
            baseline_chains["reference"],
            baseline_chains["origin-20200701"],
            candidate_chains["native-origin-20200702"],
            candidate_chains["native-origin-20200703"],
        ],
    }
    return contract, completion


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)
    Path(f"{path}.ready").write_text(sha256(path) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-contract", required=True, type=Path)
    parser.add_argument("--baseline-completion", required=True, type=Path)
    parser.add_argument("--candidate-completion", required=True, type=Path)
    parser.add_argument("--candidate-static-2", required=True, type=Path)
    parser.add_argument("--candidate-static-3", required=True, type=Path)
    parser.add_argument("--output-contract", required=True, type=Path)
    parser.add_argument("--output-completion", required=True, type=Path)
    args = parser.parse_args()
    for output in (args.output_contract, args.output_completion):
        if output.exists() or Path(f"{output}.ready").exists():
            raise SystemExit(f"refusing to overwrite publication: {output}")
    for static in (args.candidate_static_2, args.candidate_static_3):
        if not static.is_file() or not Path(f"{static}.ready").is_file():
            raise ValueError(f"candidate static is not published: {static}")

    contract, completion = merge_payloads(
        published(args.baseline_contract),
        published(args.baseline_completion),
        published(args.candidate_completion),
        args.candidate_static_2,
        args.candidate_static_3,
    )
    write_json_atomic(args.output_contract, contract)
    write_json_atomic(args.output_completion, completion)
    print(f"PASS: published {args.output_contract} and {args.output_completion}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
