from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METADATA_SOURCE = ROOT / "HICAR" / "src" / "io" / "default_output_metadata.F90"
OUTPUT_SOURCE = ROOT / "HICAR" / "src" / "io" / "output_obj.F90"
LSM_SOURCE = ROOT / "HICAR" / "src" / "physics" / "lsm_driver.F90"
CONSTANTS_SOURCE = ROOT / "HICAR" / "src" / "constants" / "icar_constants.F90"


def test_lwtr_is_described_as_downwelling_not_net_longwave() -> None:
    source = METADATA_SOURCE.read_text()
    start = source.index("else if (var_idx==kVARS%longwave) then")
    stop = source.index(
        "else if (var_idx==kVARS%rad_absorbed_total) then",
        start,
    )
    block = source[start:stop]

    assert 'var_meta%name        = "lwtr"' in block
    assert (
        'attribute_t("standard_name", '
        '"surface_downwelling_longwave_flux_in_air")'
    ) in block
    assert "surface_net_downward_longwave_flux" not in block


def test_height_agl_coordinate_write_participates_on_all_io_ranks() -> None:
    source = OUTPUT_SOURCE.read_text()
    start = source.index("if (height_var_id >= 0) then")
    stop = source.index("endif", start) + len("endif")
    block = source[start:stop]

    assert "nf90_var_par_access" in block
    assert "nf90_collective" in block
    assert "height_count = 0" in block
    assert "count=height_count" in block


def test_wind_surface_and_pbl_diagnostics_have_cf_metadata() -> None:
    source = METADATA_SOURCE.read_text()

    assert (
        'attribute_t("standard_name", '
        '"magnitude_of_surface_friction_velocity_in_air")'
    ) in source
    assert (
        'attribute_t("standard_name", '
        '"surface_roughness_length_for_momentum_in_air")'
    ) in source
    assert (
        'attribute_t("standard_name", '
        '"atmosphere_boundary_layer_thickness")'
    ) in source

    start = source.index("else if (var_idx==kVARS%br) then")
    stop = source.index("else if (var_idx==kVARS%QFX) then", start)
    richardson_block = source[start:stop]
    assert 'var_meta%name        = "sfc_Ri"' in richardson_block
    assert '"Surface-layer bulk Richardson number"' in richardson_block
    assert 'attribute_t("units",         "1")' in richardson_block
    assert '"standard_name"' not in richardson_block


def test_mol_metadata_describes_temperature_scale_not_obukhov_length() -> None:
    source = METADATA_SOURCE.read_text()
    start = source.index("else if (var_idx==kVARS%mol) then")
    stop = source.index("else if (var_idx==kVARS%ustar) then", start)
    block = source[start:stop]

    assert '"Surface-layer temperature scale"' in block
    assert 'attribute_t("units",         "K")' in block
    assert "Monin-Obukhov length" not in block


def test_runoff_snapshot_metadata_is_a_soil_step_amount_not_a_rate() -> None:
    source = METADATA_SOURCE.read_text()
    start = source.index("else if (var_idx==kVARS%runoff_surface) then")
    stop = source.index(
        "else if (var_idx==kVARS%runoff_surface_cumulative) then",
        start,
    )
    block = source[start:stop]

    assert "preceding Noah-MP soil timestep" in block
    assert 'attribute_t("units",         "kg m-2")' in block
    assert "mm s-1" not in block
    assert "surface_runoff_flux" not in block


def test_cumulative_water_observables_are_restart_persistent_and_no_reset() -> None:
    metadata = METADATA_SOURCE.read_text()
    lsm = LSM_SOURCE.read_text()
    constants = CONSTANTS_SOURCE.read_text()
    names = (
        "runoff_surface_cumulative",
        "runoff_subsurface_cumulative",
        "evaporation_net_cumulative",
    )

    for name in names:
        assert f"integer :: {name}" in constants
        assert f'var_meta%name        = "{name}"' in metadata
        assert (
            "cumulative since simulation start; no output reset; "
            "restart-persistent"
        ) in metadata
        assert lsm.count(f"kVARS%{name}") >= 3

    snow_call = lsm.index("call snow_model(domain, options, dt")
    accumulation = lsm.index(
        "runoff_surface_cumulative(i,j) = "
        "runoff_surface_cumulative(i,j)",
        snow_call,
    )
    precip_tracking = lsm.index("lsm_last_precip(i,j) = precipitation(i,j)")
    assert snow_call < accumulation < precip_tracking
    accumulation_block = lsm[snow_call:precip_tracking]
    assert "if (land_mask(i,j) == real(kLC_LAND)) then" in accumulation_block
    assert "if (runoff_surface_step(i,j) >= 0.0) then" in accumulation_block
    assert "if (runoff_subsurface_step(i,j) >= 0.0) then" in accumulation_block


def test_precipitation_contract_is_cumulative_restart_state_without_bucket_reset() -> None:
    metadata = METADATA_SOURCE.read_text()
    lsm = LSM_SOURCE.read_text()
    executable_bucket_references = [
        (path, line)
        for path in (ROOT / "HICAR" / "src").rglob("*.F90")
        for line in path.read_text(errors="replace").splitlines()
        if "precipitation_bucket" in line.split("!", 1)[0]
    ]

    start = metadata.index("else if (var_idx==kVARS%precipitation) then")
    stop = metadata.index(
        "else if (var_idx==kVARS%convective_precipitation) then",
        start,
    )
    block = metadata[start:stop]
    assert (
        "cumulative since simulation start; no output reset; "
        "restart-persistent"
    ) in block
    restart_start = lsm.index("call options%restart_vars(", lsm.index("kLSM_NOAHMP"))
    restart_stop = lsm.index("endif", restart_start)
    assert "kVARS%precipitation" in lsm[restart_start:restart_stop]
    assert executable_bucket_references == []
