from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LSM_SOURCE = ROOT / "HICAR" / "src" / "physics" / "lsm_driver.F90"
PBL_SOURCE = ROOT / "HICAR" / "src" / "physics" / "pbl_driver.F90"


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
