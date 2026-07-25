from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import netCDF4


ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "compare_segmented_to_uninterrupted.py"
)
SCIENTIFIC_PLAN = (
    ROOT / "case_studies" / "swiss_200m" / "config" / "scientific_pilot_plan.json"
)
sys.path.insert(
    0,
    str(ROOT / "case_studies" / "swiss_200m" / "streaming"),
)
from validate_model_chunk import QUALIFICATION_VARIABLES  # noqa: E402


def write_output(path: Path, hours: list[int], perturbation: float = 0.0):
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("time", len(hours))
        dataset.createDimension("y", 2)
        dataset.createDimension("x", 2)
        dataset.createDimension("soil", 4)
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "hours since 2020-07-15 00:00:00"
        time[:] = hours
        layered = {"soil_water_content", "soil_temperature"}
        for name in QUALIFICATION_VARIABLES:
            dimensions = (
                ("time", "soil", "y", "x") if name in layered else ("time", "y", "x")
            )
            variable = dataset.createVariable(name, "f8", dimensions)
            variable[:] = 1.0
        if perturbation:
            dataset.variables["taix"][-1, 0, 0] += perturbation


def publish_completion(path: Path, output: Path):
    path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "output_profile": "qualification",
                "output_interval_seconds": 10800,
                "output": {"files": [{"path": str(output)}]},
            }
        )
    )
    Path(f"{path}.ready").touch()


def run_comparison(tmp_path: Path, perturbation: float):
    segmented_first = tmp_path / "segmented_first.nc"
    segmented_second = tmp_path / "segmented_second.nc"
    reference = tmp_path / "reference.nc"
    write_output(segmented_first, [3, 6, 9, 12])
    write_output(segmented_second, [15, 18, 21, 24], perturbation)
    write_output(reference, [3, 6, 9, 12, 15, 18, 21, 24])
    first_completion = tmp_path / "first.json"
    second_completion = tmp_path / "second.json"
    reference_completion = tmp_path / "reference.json"
    publish_completion(first_completion, segmented_first)
    publish_completion(second_completion, segmented_second)
    publish_completion(reference_completion, reference)
    report = tmp_path / "comparison.json"
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARATOR),
            "--segmented-completion",
            str(first_completion),
            "--segmented-completion",
            str(second_completion),
            "--reference-completion",
            str(reference_completion),
            "--scientific-plan",
            str(SCIENTIFIC_PLAN),
            "--start",
            "2020-07-15T00:00:00",
            "--end",
            "2020-07-16T00:00:00",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
    )
    return result, json.loads(report.read_text()), report


def test_segmented_trajectory_matches_uninterrupted_reference(tmp_path):
    result, report, path = run_comparison(tmp_path, perturbation=0.0)

    assert result.returncode == 0, result.stderr + result.stdout
    assert report["status"] == "PASS"
    assert len(report["expected_times"]) == 8
    assert report["metrics"]["taix"]["maximum_absolute_error"] == 0.0
    assert Path(f"{path}.ready").is_file()


def test_out_of_tolerance_post_restart_difference_fails(tmp_path):
    result, report, path = run_comparison(tmp_path, perturbation=1.0)

    assert result.returncode != 0
    assert report["status"] == "FAIL"
    assert report["metrics"]["taix"]["outside_tolerance_count"] == 1
    assert not Path(f"{path}.ready").exists()
