from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LSM_SOURCE = ROOT / "HICAR" / "src" / "physics" / "lsm_driver.F90"
PBL_SOURCE = ROOT / "HICAR" / "src" / "physics" / "pbl_driver.F90"
DOMAIN_SOURCE = ROOT / "HICAR" / "src" / "objects" / "domain_obj.F90"
NOAHMP_PATCH = ROOT / "HICAR" / "cmake" / "patch_noahmp_glacier_snow.cmake"


def test_lsm_cold_start_guesses_do_not_overwrite_restart_state() -> None:
    source = LSM_SOURCE.read_text()
    start = source.index("! Initial guesses are cold-start state only.")
    stop = source.index("! Noah-MP Land Surface Model", start)
    block = source[start:stop]

    assert "if (.not. restart) then" in block
    assert "temperature_2m(i,j) = temperature(i,kms,j)" in block
    assert "humidity_2m(i,j) = water_vapor(i,kms,j)" in block


def test_lsm_soil_clamps_are_cold_start_only() -> None:
    source = LSM_SOURCE.read_text()
    start = source.index("! Cold-start protection for water points")
    stop = source.index("IDVEG = options%lsm%nmp_dveg", start)
    block = source[start:stop]

    assert "if (.not. restart) then" in block
    assert "soil_temperature(i,k,j) = 200.0" in block
    assert "soil_water_content(i,k,j) = 0.0001" in block


def test_pbl_initializer_recognizes_file_restarts() -> None:
    source = PBL_SOURCE.read_text()

    assert "restart = context_change .or. options%restart%restart" in source
    assert ",restart=restart" in source


def test_pbl_restart_tendencies_are_not_zeroed() -> None:
    source = PBL_SOURCE.read_text()
    start = source.index("! initialize tendencies")
    stop = source.index("call ysuinit_gpu", start)
    block = source[start:stop]

    assert "if(.not.restart)then" in block
    assert "if(.not.context_change)then" not in block


def test_bulk_snow_temperature_is_a_cold_start_only_noahmp_override() -> None:
    source = LSM_SOURCE.read_text()
    assert 'options%domain%snow_temp_var /= ""' in source
    assert "use_input_snow_temperature = (.not. restart)" in source
    assert "nmp_snow_t(i,k,j) = snow_temperature(i,k,j)" in source
    assert "options%domain%snow_temp_var" in DOMAIN_SOURCE.read_text()


def test_bulk_snow_temperature_uses_initialized_noahmp_layer_count() -> None:
    source = LSM_SOURCE.read_text()
    start = source.index("! Copy NoahMP-sized scratch back")
    stop = source.index(
        "!$acc parallel loop gang vector collapse(2) present(veg_type", start
    )
    block = source[start:stop]

    assert "snow_nlayers => domain%vars_2d" in block
    assert "num_snow_layers + snow_nlayers(i,j)" in block
    assert "num_snow_layers + nmp_snow_nlayers(i,j)" not in block


def test_pinned_noahmp_patch_preserves_supplied_glacier_snow() -> None:
    source = NOAHMP_PATCH.read_text()
    assert "Preserve caller-provided glacier SWE and snow depth" in source
    assert "refusing an unverified patch" in source
