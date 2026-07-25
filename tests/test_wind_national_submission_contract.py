from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SUBMITTER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "wind_climatology"
    / "submit_national_wind_stream_gate_balfrin.sh"
)


def touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_national_submission_rejects_stale_stream_runner(tmp_path):
    scratch = tmp_path / "scratch"
    validation = scratch / "icon_hicar" / "validation" / "wind-climatology"
    case = scratch / "icon_hicar" / "case_studies" / "swiss_200m"
    stream = validation / "national-stream"
    for chunk_id in (
        "wind-national-v2-20100101-0000-0100",
        "wind-national-v2-20100101-0100-0200",
    ):
        plan = stream / chunk_id / "chunk_plan.json"
        touch(plan, "{}")
        touch(Path(f"{plan}.ready"))
    touch(
        case / "scripts" / "run_rea_l_stream_chunk_balfrin.sbatch",
        "case \"$output_profile\" in routine|qualification) ;; esac\n",
    )
    touch(
        case / "scripts" / "render_hicar_namelist.py",
        'OUTPUT_PROFILES = {"wind_climatology": ()}\n',
    )
    touch(
        case / "config" / "hicar_swiss_200m.nml.in",
        "@OUTPUT_VARS@\n",
    )
    touch(
        case / "streaming" / "validate_model_chunk.py",
        "WIND_CLIMATOLOGY_REQUIRED_VARIABLES = set()\n",
    )
    touch(
        scratch / "icon_hicar" / "scripts" / "reduce_hicar_wind_climatology.py"
    )

    environment = os.environ.copy()
    environment.update(
        {
            "SCRATCH": str(scratch),
            "VALIDATION_ROOT": str(validation),
            "HICAR_SWISS_CASE": str(case),
            "HICAR_SOURCE_ROOT": str(tmp_path / "source"),
            "HICAR_BUILD_ROOT": str(tmp_path / "build"),
        }
    )
    result = subprocess.run(
        ["bash", str(SUBMITTER)],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "stream runner does not support wind_climatology" in result.stderr
