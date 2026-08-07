#!/usr/bin/env python3
"""Publish matched cloud/radiation/land evidence for the V29 diagnostic run.

This assessor is deliberately non-promoting.  The REA-L sidecar contains
instantaneous cloud cover but interval-mean radiation and turbulent fluxes,
whereas HICAR history fields are snapshots.  It therefore reports those two
comparison classes separately and leaves any correction authorization to the
predeclared scientific decision rule.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


REQUIRED_HICAR = (
    "cldfrac", "precipitation", "snowfall", "graupel", "qc", "qi", "qr", "qs", "qg",
    "swtb", "swtd", "lwtr", "hfss", "hfls",
    "soil_column_total_water", "soil_water_content", "soil_temperature",
)
REQUIRED_REFERENCE = (
    "cloud_area_fraction_ref", "rain_interval_ref", "snow_interval_ref", "graupel_interval_ref",
    "sw_direct_down_interval_ref", "sw_diffuse_down_interval_ref",
    "lw_down_interval_ref", "latent_heat_flux_interval_ref",
    "sensible_heat_flux_interval_ref",
)
REFERENCE_RADIATION_AND_FLUX = (
    "sw_direct_down_interval_ref",
    "sw_diffuse_down_interval_ref",
    "lw_down_interval_ref",
    "latent_heat_flux_interval_ref",
    "sensible_heat_flux_interval_ref",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def times(dataset: netCDF4.Dataset) -> list[datetime]:
    variable = dataset.variables["time"]
    return [
        datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
        for value in netCDF4.num2date(variable[:], variable.units, calendar=getattr(variable, "calendar", "standard"))
    ]


def mean(field: np.ndarray, mask: np.ndarray) -> float:
    values = np.ma.asarray(field)
    if values.shape[-2:] != mask.shape:
        raise ValueError(f"field shape {values.shape} does not match active-land mask {mask.shape}")
    selected = np.ma.asarray(values[..., mask], dtype=np.float64)
    if np.ma.is_masked(selected) or not np.all(np.isfinite(selected)):
        raise ValueError("field has masked or non-finite active-land values")
    return float(np.mean(selected))


def reference_mean(field: np.ndarray, latitude: np.ndarray | None) -> float:
    """Area-weight a finite REA-L regular-latitude grid independently.

    HICAR's static mask belongs to its 200-m projected grid and must never be
    applied to a REA-L sidecar on its forcing grid.  The two spatial means are
    thus comparable domain summaries, not pointwise/regridded comparisons.
    """
    values = np.ma.asarray(field, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError(f"reference field must be two-dimensional, got {values.shape}")
    if np.ma.is_masked(values) or not np.all(np.isfinite(values)):
        raise ValueError("reference field has masked or non-finite values")
    if latitude is None:
        return float(np.mean(values))
    latitude = np.asarray(latitude, dtype=np.float64)
    if latitude.ndim != 1 or latitude.size != values.shape[0]:
        raise ValueError("reference latitude does not match field rows")
    weights = np.cos(np.deg2rad(latitude))[:, None]
    return float(np.sum(values * weights) / (values.shape[1] * np.sum(weights)))


def reference_path(root: Path, valid: datetime) -> Path:
    return root / f"rea_l_surface_reference_{valid:%Y%m%d_%H%M}.nc"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--static-file", required=True, type=Path)
    parser.add_argument("--hicar-history", required=True, type=Path, action="append")
    parser.add_argument("--reference-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text())
    baseline = contract.get("baseline", {})
    if contract.get("status") != "PREDECLARED_NOT_YET_RUN":
        raise SystemExit("mechanism contract is not the predeclared baseline")
    if baseline.get("output_profile") != "mechanism_diagnosis":
        raise SystemExit("mechanism contract has the wrong output profile")
    with netCDF4.Dataset(args.static_file) as static:
        landmask = np.asarray(static.variables["landmask"][:]) > 0
        landuse = np.asarray(static.variables["landuse"][:])
    active_land = landmask & (landuse != 16) & (landuse != 24)
    if active_land.ndim != 2 or not np.any(active_land):
        raise SystemExit("static active-land mask is empty or invalid")

    records: list[dict] = []
    initial_soil: dict[str, float] | None = None
    previous_hydrometeors: dict[str, float] | None = None
    for path in args.hicar_history:
        with netCDF4.Dataset(path) as history:
            missing = sorted(set(REQUIRED_HICAR) - set(history.variables))
            if missing:
                raise SystemExit(f"{path} lacks diagnostic fields: {', '.join(missing)}")
            for index, valid in enumerate(times(history)):
                first_record = not records
                hicar = {name: mean(history.variables[name][index], active_land) for name in REQUIRED_HICAR}
                soil = {name: hicar[name] for name in ("soil_column_total_water", "soil_water_content", "soil_temperature")}
                if initial_soil is None:
                    initial_soil = soil
                hydrometeors = {
                    name: hicar[name] for name in ("precipitation", "snowfall", "graupel")
                }
                hicar_intervals = (
                    None
                    if previous_hydrometeors is None
                    else {
                        "precipitation": hydrometeors["precipitation"] - previous_hydrometeors["precipitation"],
                        "snow": hydrometeors["snowfall"] - previous_hydrometeors["snowfall"],
                        "graupel": hydrometeors["graupel"] - previous_hydrometeors["graupel"],
                        "rain": (
                            hydrometeors["precipitation"] - hydrometeors["snowfall"] - hydrometeors["graupel"]
                            - previous_hydrometeors["precipitation"] + previous_hydrometeors["snowfall"] + previous_hydrometeors["graupel"]
                        ),
                    }
                )
                previous_hydrometeors = hydrometeors
                source = reference_path(args.reference_dir, valid)
                if not source.is_file() or not Path(f"{source}.ready").is_file():
                    raise SystemExit(f"missing published source sidecar for {valid.isoformat()}: {source}")
                with netCDF4.Dataset(source) as reference:
                    available = set(reference.variables)
                    if first_record:
                        # The archived first sample establishes the initial
                        # state but intentionally has no preceding interval.
                        ref = None
                    else:
                        missing = sorted(set(REQUIRED_REFERENCE) - available)
                        if missing:
                            raise SystemExit(f"{source} lacks sidecar fields: {', '.join(missing)}")
                        latitude = (
                            reference.variables["latitude"][:]
                            if "latitude" in reference.variables
                            else None
                        )
                        ref = {
                            name: reference_mean(reference.variables[name][0], latitude)
                            for name in REQUIRED_REFERENCE
                        }
                record = {
                    "valid_time": valid.isoformat(),
                    "cloud_instantaneous": None if first_record else {
                        "comparison_semantics": (
                            "HICAR cldfrac and REA-L cloud_area_fraction_ref are "
                            "instantaneous cloud_area_fraction at this valid time; each is a "
                            "separately aggregated native-grid domain mean, not a pointwise "
                            "or common-mask comparison"
                        ),
                        "hicar": hicar["cldfrac"], "rea_l": ref["cloud_area_fraction_ref"],
                        "hicar_minus_rea_l": hicar["cldfrac"] - ref["cloud_area_fraction_ref"],
                    },
                    "hydrometeor_column_means": {
                        name: hicar[name] for name in ("qc", "qi", "qr", "qs", "qg")
                    },
                    "precipitation_intervals": None if first_record else {
                        "hicar": hicar_intervals,
                        "rea_l": {
                            "rain": ref["rain_interval_ref"],
                            "snow": ref["snow_interval_ref"],
                            "graupel": ref["graupel_interval_ref"],
                        },
                    },
                    "radiation_and_flux": None if first_record else {
                        "comparison_semantics": {
                            "time_alignment": (
                                "HICAR snapshots versus REA-L ending three-hour interval means; "
                                "not used for lead/lag inference"
                            ),
                            "directly_comparable_quantities": {
                                "swtb": "surface direct downwelling shortwave flux",
                                "swtd": "surface diffuse downwelling shortwave flux",
                                "hfss": "surface upward sensible heat flux",
                                "hfls": "surface upward latent heat flux",
                            },
                            "not_directly_comparable": {
                                "lwtr": (
                                    "HICAR lwtr is surface net downward longwave flux, whereas "
                                    "REA-L lw_down_interval_ref is surface downwelling longwave flux"
                                )
                            },
                        },
                        "hicar": {name: hicar[name] for name in ("swtb", "swtd", "lwtr", "hfss", "hfls")},
                        "rea_l_interval": {
                            name: ref[name] for name in REFERENCE_RADIATION_AND_FLUX
                        },
                    },
                    "land_state_change_from_initial": {name: soil[name] - initial_soil[name] for name in soil},
                }
                records.append(record)

    if len(records) != 9:
        raise SystemExit(f"expected nine 3-hourly records for the 24-hour baseline, got {len(records)}")
    payload = {
        "schema_version": 1,
        "status": "PASS_NON_PROMOTING",
        "decision": "SCIENTIFIC_REVIEW_REQUIRED",
        "reason": (
            "Cloud-cover and precipitation comparisons are time matched. Radiation and turbulent fluxes retain "
            "different snapshot/interval semantics, so this report deliberately cannot claim a causal lead from them."
        ),
        "contract": str(args.contract.resolve()),
        "contract_sha256": sha256(args.contract),
        "assessor": str(Path(__file__).resolve()),
        "assessor_sha256": sha256(Path(__file__).resolve()),
        "static_file": str(args.static_file.resolve()),
        "history": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in args.hicar_history],
        "reference_dir": str(args.reference_dir.resolve()),
        "active_land_cells": int(np.count_nonzero(active_land)),
        "spatial_aggregation": {
            "hicar": "unweighted active USGS land mean on the 200 m projected grid",
            "rea_l": (
                "cos(latitude)-weighted finite-cell mean on the native REA-L forcing grid; "
                "a domain summary, not a pointwise or regridded comparison"
            ),
        },
        "records": records,
        "next_authorization": "At most one correction only after the scientific decision rule identifies a leading mechanism; otherwise HOLD.",
    }
    write_atomic(args.report, payload)
    Path(f"{args.report}.ready").touch()
    print(f"PASS_NON_PROMOTING: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
