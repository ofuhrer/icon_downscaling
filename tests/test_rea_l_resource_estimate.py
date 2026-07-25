from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "estimate_rea_l_production.py"
)
SPEC = importlib.util.spec_from_file_location("resource_estimator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_balfrin_capacity_waves_replace_impossible_all_year_parallelism():
    case_200m = MODULE.compute_case(
        label="200m",
        cells=3_749_421,
        nodes=4,
        wall_factor=1.0,
        uncertainty=(0.8, 1.5),
    )
    case_100m = MODULE.compute_case(
        label="100m",
        cells=14_989_841,
        nodes=16,
        wall_factor=2.0,
        uncertainty=(1.5, 3.0),
    )

    assert case_200m["unconstrained_twenty_parallel_year_chains_nodes"] == 80
    assert case_200m["balfrin_normal_capacity"][
        "maximum_concurrent_year_chains"
    ] == 11
    assert case_200m["balfrin_normal_capacity"][
        "year_chain_waves_for_twenty_years"
    ] == 2
    assert case_100m["unconstrained_twenty_parallel_year_chains_nodes"] == 320
    assert case_100m["balfrin_normal_capacity"][
        "maximum_concurrent_year_chains"
    ] == 2
    assert case_100m["balfrin_normal_capacity"][
        "year_chain_waves_for_twenty_years"
    ] == 10
    assert case_100m["seven_day_chunk_fits_normal_24h_at_range_high"] is False
