from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCREEN = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "screen_scientific_month_drift.py"
)
SCIENTIFIC_PLAN = (
    ROOT / "case_studies" / "swiss_200m" / "config" / "scientific_pilot_plan.json"
)


def publish(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def run_screen(tmp_path: Path, monotonic_temperature: bool) -> dict:
    plan = json.loads(SCIENTIFIC_PLAN.read_text())
    drift = plan["promotion_criteria"]["month_to_annual_cycle"][
        "postspinup_drift_review"
    ]
    start = datetime(2020, 7, 1)
    times = [start + timedelta(hours=3 * index) for index in range(249)]
    classes = {}
    for class_name in drift["classes"]:
        series = {"times": [value.isoformat() for value in times]}
        for variable in drift["variables"]:
            series[variable] = [1.0 for _ in times]
        classes[class_name] = {"time_series": series}
    if monotonic_temperature:
        classes["active_soil_interior"]["time_series"]["temperature_2m_k"] = [
            280.0 + (4.0 * index / 192.0) if index >= 56 else 280.0
            for index in range(249)
        ]

    month = tmp_path / "month.json"
    diagnostics = tmp_path / "diagnostics.json"
    report = tmp_path / "drift.json"
    publish(month, {"status": "PLANNED", "start": start.isoformat()})
    publish(diagnostics, {"status": "PASS", "classes": classes})
    result = subprocess.run(
        [
            sys.executable,
            str(SCREEN),
            "--month-plan",
            str(month),
            "--scientific-plan",
            str(SCIENTIFIC_PLAN),
            "--diagnostics",
            str(diagnostics),
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert Path(f"{report}.ready").is_file()
    return json.loads(report.read_text())


def test_stationary_month_has_no_drift_flags(tmp_path):
    report = run_screen(tmp_path, monotonic_temperature=False)

    assert report["status"] == "PASS"
    assert report["decision"] == "NO_DRIFT_FLAGS"
    assert report["flag_count"] == 0


def test_large_monotonic_temperature_tendency_requires_attribution(tmp_path):
    report = run_screen(tmp_path, monotonic_temperature=True)

    assert report["status"] == "PASS"
    assert report["decision"] == "ATTRIBUTION_REQUIRED"
    assert {
        item["id"] for item in report["flags"]
    } == {"active_soil_interior:temperature_2m_k"}
