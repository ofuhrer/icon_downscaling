from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "streaming"
    / "create_chunk_plan.py"
)
SPEC = importlib.util.spec_from_file_location("streaming_plan", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_chunk_plan_uses_new_cycle_step_zero_at_midnight(tmp_path):
    records = MODULE.records_for_period(
        datetime.fromisoformat("2009-12-31T23:00:00"),
        datetime.fromisoformat("2010-01-01T01:00:00"),
        tmp_path,
    )
    assert [(record["cycle_date"], record["step_hours"]) for record in records] == [
        ("20091231", 23),
        ("20100101", 0),
        ("20100101", 1),
    ]
    assert [record["valid_time"] for record in records] == [
        "2009-12-31T23:00:00",
        "2010-01-01T00:00:00",
        "2010-01-01T01:00:00",
    ]
