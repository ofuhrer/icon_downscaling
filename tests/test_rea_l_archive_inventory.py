from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "inventory_rea_l_archive.py"
)
SPEC = importlib.util.spec_from_file_location("rea_l_inventory", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_inventory_detects_complete_and_missing_periods(tmp_path):
    for day in ("20050101", "20050102", "20050104"):
        (tmp_path / f"{day}:0000:reanl:rd:icon-rea-l-ch1:r001:cf").mkdir()
    report = MODULE.inventory(tmp_path, date(2005, 1, 1), date(2005, 1, 2))
    assert report["status"] == "PASS"
    assert report["available"]["daily_cycles"] == 3
    assert report["available"]["missing_cycles_between_first_and_last"] == ["2005-01-03"]

    report = MODULE.inventory(tmp_path, date(2005, 1, 1), date(2005, 1, 4))
    assert report["status"] == "FAIL"
    assert report["production_period"]["missing_cycles"] == ["2005-01-03"]
