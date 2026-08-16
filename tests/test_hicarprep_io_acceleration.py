from __future__ import annotations

import json
from pathlib import Path

import netCDF4
import numpy as np
import pytest

from preprocessing.hicarprep import icon_atmosphere
from preprocessing.hicarprep.icon_atmosphere import native_storage_options
from preprocessing.hicarprep.products import sha256
from preprocessing.hicarprep.publication import (
    relocate_publication_receipt,
    validate_publication_receipt,
)
from preprocessing.hicarprep.sst import SST_POLICY_VERSION, SST_REMAP_POLICY


def _publication_fixture(root: Path) -> tuple[Path, Path, Path]:
    static = root / "static.nc"
    forcing = root / "forcing.nc"
    receipt = root / "forcing.manifest.json"
    with netCDF4.Dataset(static, "w") as dataset:
        for name, size in (("y", 2), ("x", 2), ("level", 2), ("half_level", 3)):
            dataset.createDimension(name, size)
        for name, dimensions in (
            ("lat", ("y", "x")),
            ("lon", ("y", "x")),
            ("landmask", ("y", "x")),
            ("HFL", ("level", "y", "x")),
            ("HHL", ("half_level", "y", "x")),
        ):
            dataset.createVariable(name, "f8", dimensions)[:] = 1.0
    static_digest = sha256(static)
    with netCDF4.Dataset(forcing, "w") as dataset:
        for name, size in (("time", 1), ("z", 2), ("z_hl", 3), ("y_1", 2), ("x_1", 2)):
            dataset.createDimension(name, size)
        dataset.product_type = "hicarprep_target_forcing_record"
        dataset.static_sha256 = static_digest
        dataset.water_representation = "dry-air mixing ratio"
        dataset.target_w_vertical_coordinate = "authoritative_static_HFL"
        dataset.target_w_terrain_wind_basis = "HICAR_grid_relative"
        dataset.geometry_serialization = "static_sleve_with_one_ulp_top_cover"
        dataset.sst_policy_version = SST_POLICY_VERSION
        dataset.sst_remap_policy = SST_REMAP_POLICY
        dataset.valid_time = "2020-01-01T01:00:00Z"
        dataset.createVariable("lat_1", "f8", ("y_1", "x_1"))[:] = 46.0
        dataset.createVariable("lon_1", "f8", ("y_1", "x_1"))[:] = 7.0
        for name in ("P", "T", "QV", "QC", "QI", "U", "V", "W"):
            dataset.createVariable(name, "f4", ("time", "z", "y_1", "x_1"))[:] = 1.0
        dataset.createVariable("SST", "f4", ("time", "y_1", "x_1"))[:] = 280.0
        dataset.createVariable("HFL", "f4", ("z", "y_1", "x_1"))[:] = 1.0
        dataset.createVariable("HHL", "f4", ("z_hl", "y_1", "x_1"))[:] = 1.0
        dataset.createVariable("HSURF", "f4", ("y_1", "x_1"))[:] = 1.0
        dataset.createVariable("FR_LAND", "f4", ("y_1", "x_1"))[:] = 1.0
        dataset.createVariable(
            "SST_unsupported_water_mask", "i1", ("y_1", "x_1")
        )[:] = 0
        dataset.createVariable(
            "SST_nearest_same_surface_candidate_distance_km", "f8", ("y_1", "x_1")
        )[:] = np.nan
        time = dataset.createVariable("time", "f8", ("time",))
        time.units = "seconds since 1970-01-01 00:00:00 UTC"
        time.calendar = "gregorian"
        time[:] = 1_577_840_400.0
    forcing_digest = sha256(forcing)
    payload = {
        "schema": "hicarprep-target-forcing-manifest-v1",
        "status": "PASS",
        "valid_time": "2020-01-01T01:00:00",
        "source": {"path": str(root / "native.nc"), "sha256": "1" * 64},
        "static": {"path": str(static), "sha256": static_digest},
        "target_sst": {"path": str(root / "sst.nc"), "sha256": "2" * 64},
        "weights": {"path": str(root / "weights.nc"), "sha256": "3" * 64},
        "output": {"path": str(forcing), "sha256": forcing_digest},
        "forcing_file": str(forcing),
        "forcing_sha256": forcing_digest,
        "water_representation": "dry-air mixing ratio",
        "lateral_relaxation_authority": "HICAR regular forcing relax_filters",
    }
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    return forcing, static, receipt


def test_native_adapter_storage_is_lossless_level_one_by_default_contract() -> None:
    assert native_storage_options(1) == {"zlib": True, "complevel": 1, "shuffle": True}
    assert native_storage_options(0) == {}
    with pytest.raises(ValueError, match="0..9"):
        native_storage_options(10)
    with pytest.raises(ValueError, match="integer"):
        native_storage_options(True)


def test_native_decoder_writes_level_one_and_uncompressed_without_value_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the strict synthetic GRIB inventory maintained by the decoder tests.
    from test_icon_atmosphere import inventories, write_extpar

    dynamic, geometry = inventories()
    dynamic_path = tmp_path / "dynamic.grib"
    geometry_path = tmp_path / "geometry.grib"
    extpar_path = tmp_path / "extpar.nc"
    dynamic_path.write_bytes(b"dynamic")
    geometry_path.write_bytes(b"geometry")
    write_extpar(extpar_path)
    monkeypatch.setattr(
        icon_atmosphere,
        "read_grib_fields",
        lambda path: dynamic if path == dynamic_path else geometry,
    )
    outputs = []
    for level in (1, 0):
        output = tmp_path / f"native-level-{level}.nc"
        icon_atmosphere.decode_icon_atmosphere(
            dynamic_path,
            geometry_path,
            extpar_path,
            "2020-02-10T01:00:00Z",
            output,
            missing_qi_policy="source-absent-zero",
            compression_level=level,
        )
        outputs.append(output)
    with netCDF4.Dataset(outputs[0]) as compressed, netCDF4.Dataset(outputs[1]) as raw:
        assert compressed["T"].filters()["complevel"] == 1
        assert compressed["T"].filters()["zlib"]
        assert not raw["T"].filters()["zlib"]
        assert compressed.missing_qi_policy == "source_absent_zero"
        assert compressed["QI"].source_policy == "source_absent_zero"
        for name in ("P", "T", "QV", "QC", "QI", "U", "V", "W", "HHL"):
            np.testing.assert_array_equal(compressed[name][:], raw[name][:])


def test_publication_receipt_accepts_closed_file_and_rejects_payload_corruption(
    tmp_path: Path,
) -> None:
    forcing, static, receipt = _publication_fixture(tmp_path)
    report = validate_publication_receipt(
        forcing,
        static,
        receipt,
        expected_valid_time="2020-01-01T01:00:00Z",
        expected_static_sha256=sha256(static),
    )
    assert report["forcing_sha256"] == sha256(forcing)

    with netCDF4.Dataset(forcing, "a") as dataset:
        dataset["T"][0, 1, 1, 1] = 999.0
    with pytest.raises(ValueError, match="closed forcing file"):
        validate_publication_receipt(forcing, static, receipt)


def test_publication_receipt_fails_closed_on_missing_or_forged_identity(tmp_path: Path) -> None:
    forcing, static, receipt = _publication_fixture(tmp_path)
    with pytest.raises(ValueError, match="cannot read publication receipt"):
        validate_publication_receipt(forcing, static, tmp_path / "missing.json")

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["static"]["sha256"] = "f" * 64
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="static identity"):
        validate_publication_receipt(forcing, static, receipt)


def test_publication_receipt_can_follow_validated_atomic_rename(tmp_path: Path) -> None:
    forcing, static, receipt = _publication_fixture(tmp_path)
    published = tmp_path / "published.nc"
    validate_publication_receipt(
        forcing,
        static,
        receipt,
        expected_static_sha256=sha256(static),
    )
    relocate_publication_receipt(receipt, forcing, published)
    forcing.rename(published)
    report = validate_publication_receipt(
        published,
        static,
        receipt,
        expected_static_sha256=sha256(static),
    )
    assert report["forcing_sha256"] == sha256(published)
