#!/usr/bin/env python3
"""Prepare, but do not activate, a canonical plan for a passed new baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from month_source_contract import (
    SCIENTIFIC_BASELINE_TRANSITION,
    validate_month_source_qualification,
)


PASS_DECISION = "NOMINATE_V29_FOR_CANONICAL_MONTH_SOURCE"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def published_json(path: Path, label: str) -> dict:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")
    return load_json(path)


def relative_reference(target: Path, from_directory: Path) -> str:
    return os.path.relpath(target.resolve(), from_directory.resolve())


def prepare(
    *,
    scientific_plan: dict,
    source_qualification: dict,
    source_qualification_path: Path,
    transition_report: dict,
    transition_report_path: Path,
    output_path: Path,
) -> dict:
    candidate = source_qualification.get("child_commit")
    parent = source_qualification.get("parent_commit")
    previous = source_qualification.get("previous_scientific_baseline_commit")
    failures = validate_month_source_qualification(
        source_qualification,
        expected_child_commit=candidate,
        required_parent_commit=parent,
        qualification_mode=SCIENTIFIC_BASELINE_TRANSITION,
    )
    if failures:
        raise ValueError(
            "baseline-transition source qualification failed: "
            + "; ".join(failures)
        )
    transition_evidence = source_qualification["evidence"][
        "baseline_transition"
    ]
    if (
        transition_report.get("status") != "PASS"
        or transition_report.get("decision") != PASS_DECISION
        or transition_report.get("candidate_commit") != candidate
        or sha256(transition_report_path)
        != transition_evidence.get("artifact_sha256")
    ):
        raise ValueError(
            "transition report does not match the qualified baseline source"
        )

    result = json.loads(json.dumps(scientific_plan))
    configuration = result["configuration"]
    if configuration.get("event_expected_hicar_commit") != previous:
        raise ValueError(
            "canonical plan event source is not the preserved previous baseline"
        )
    if configuration.get("month_expected_hicar_commit") is not None:
        raise ValueError("canonical plan already freezes a month source")
    configuration.update(
        {
            "previous_scientific_baseline_hicar_commit": previous,
            "event_expected_hicar_commit": candidate,
            "month_expected_hicar_commit": candidate,
            "month_required_parent_hicar_commit": parent,
            "month_source_qualification_mode": (
                SCIENTIFIC_BASELINE_TRANSITION
            ),
            "month_source_qualification_report": relative_reference(
                source_qualification_path,
                output_path.parent,
            ),
            "baseline_transition_report": relative_reference(
                transition_report_path,
                output_path.parent,
            ),
            "baseline_transition_report_sha256": sha256(
                transition_report_path
            ),
        }
    )
    result["source_selection"] = {
        "status": "NOMINATED_NOT_COMPUTE_AUTHORIZED",
        "qualification_mode": SCIENTIFIC_BASELINE_TRANSITION,
        "selected_hicar_commit": candidate,
        "source_parent_hicar_commit": parent,
        "previous_scientific_baseline_hicar_commit": previous,
        "source_qualification_report": relative_reference(
            source_qualification_path,
            output_path.parent,
        ),
        "source_qualification_sha256": sha256(source_qualification_path),
        "baseline_transition_report": relative_reference(
            transition_report_path,
            output_path.parent,
        ),
        "baseline_transition_report_sha256": sha256(transition_report_path),
        "authorization": {
            "month_compute": False,
            "annual_cycle": False,
            "twenty_year_200m_production": False,
            "hundred_meter_scientific_production": False,
        },
    }
    return result


def publish_json(path: Path, payload: dict) -> None:
    marker = Path(f"{path}.ready")
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists() or marker.exists():
        if (
            path.is_file()
            and marker.is_file()
            and path.read_text(encoding="utf-8") == serialized
        ):
            return
        raise ValueError(f"refusing to replace non-identical plan: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write(serialized)
        temporary = Path(stream.name)
    os.replace(temporary, path)
    marker.touch()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scientific-plan", type=Path, required=True)
    parser.add_argument("--source-qualification", type=Path, required=True)
    parser.add_argument("--transition-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.scientific_plan.is_file():
        raise SystemExit(f"canonical scientific plan is missing: {args.scientific_plan}")
    try:
        qualification = published_json(
            args.source_qualification, "baseline source qualification"
        )
        transition = published_json(
            args.transition_report, "baseline transition report"
        )
        payload = prepare(
            scientific_plan=load_json(args.scientific_plan),
            source_qualification=qualification,
            source_qualification_path=args.source_qualification,
            transition_report=transition,
            transition_report_path=args.transition_report,
            output_path=args.output,
        )
        publish_json(args.output, payload)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    print(f"PASS: candidate canonical scientific plan published at {args.output}")
    print("No month, annual, 20-year, or 100 m compute is authorized by this step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
