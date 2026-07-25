from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "config"
    / "observational_validation_contract.json"
)


def test_wind_contract_keeps_mean_and_gust_channels_duration_explicit() -> None:
    wind = json.loads(CONTRACT.read_text())["datasets"]["wind"]
    channels = wind["observation_channels"]

    assert channels == {
        "hourly_mean_speed": "fkl010h0",
        "hourly_mean_direction": "dkl010h0",
        "hourly_maximum_one_second_speed": "fkl010h1",
    }
    assert "one-second" in wind["sampling"]
    assert "three-second" in wind["policy"]
    assert "must not be relabeled or compared directly" in wind["policy"]
    assert "ICON VMAX is excluded" in wind["policy"]
