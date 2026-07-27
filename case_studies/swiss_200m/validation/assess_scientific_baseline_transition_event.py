#!/usr/bin/env python3
"""Apply all frozen event and baseline-transition gates to one seasonal run."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile


def load_sibling(filename: str, module_name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EVENT_ASSESSOR = load_sibling(
    "assess_scientific_pilot_events.py",
    "_baseline_transition_event_criteria",
)
TRANSITION_ASSESSOR = load_sibling(
    "assess_scientific_baseline_transition.py",
    "_baseline_transition_contract",
)


def expected_axis(plan: dict, event_name: str) -> list[str]:
    period = plan["reference_periods"][f"{event_name}_event"]
    start = datetime.fromisoformat(period["start"])
    interval_seconds = int(plan["configuration"]["output_interval_seconds"])
    interval = timedelta(seconds=interval_seconds)
    record_count = (
        int(period["duration_hours"]) * 3600 // interval_seconds
    ) + 1
    return [(start + index * interval).isoformat() for index in range(record_count)]


def assess_event_transition(
    *,
    event_name: str,
    run_dir: Path,
    candidate_plan: dict,
    transition_contract: dict,
) -> dict:
    criteria = {
        **candidate_plan["promotion_criteria"]["event_to_month"],
        "output_interval_seconds": candidate_plan["configuration"][
            "output_interval_seconds"
        ],
    }
    candidate = transition_contract["candidate_commit"]
    scientific = EVENT_ASSESSOR.assess_event(
        event_name,
        run_dir,
        criteria,
        candidate,
        expected_axis(candidate_plan, event_name),
    )
    transition, transition_failures = TRANSITION_ASSESSOR.assess_event(
        event_name,
        run_dir,
        transition_contract,
    )
    failures = [
        *(f"scientific:{item}" for item in scientific.get("failed_screens", [])),
        *(f"transition:{item}" for item in transition_failures),
    ]
    passed = (
        scientific.get("complete") is True
        and scientific.get("decision") == "PASS"
        and transition.get("status") == "PASS"
        and not failures
    )
    return {
        "schema_version": 1,
        "status": "PASS" if passed else "FAIL",
        "decision": "PASS_EVENT_TRANSITION" if passed else "HOLD_AND_DIAGNOSE",
        "event": event_name,
        "candidate_commit": candidate,
        "run_dir": str(run_dir.resolve()),
        "scientific_event_assessment": scientific,
        "water_and_identity_assessment": transition,
        "failures": failures,
        "authorization": {
            "matching_season_event": passed and event_name == "summer",
            "summer_restart_overlap": passed and event_name == "summer",
            "paired_transition_assessment": False,
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
        },
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", choices=("summer", "winter"), required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--candidate-plan", type=Path, required=True)
    parser.add_argument("--transition-contract", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    try:
        candidate_plan = TRANSITION_ASSESSOR.published_json(args.candidate_plan)
        contract = TRANSITION_ASSESSOR.published_json(args.transition_contract)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if contract.get("status") != "FROZEN":
        raise SystemExit("transition assessment contract is not FROZEN")
    if (
        candidate_plan.get("configuration", {}).get(
            "event_expected_hicar_commit"
        )
        != contract.get("candidate_commit")
    ):
        raise SystemExit("candidate plan and transition contract disagree")

    payload = assess_event_transition(
        event_name=args.event_name,
        run_dir=args.run_dir,
        candidate_plan=candidate_plan,
        transition_contract=contract,
    )
    atomic_json(args.report, payload)
    Path(f"{args.report}.ready").touch()
    print(f"{payload['decision']}: {args.event_name} transition event assessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
