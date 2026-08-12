from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/hicarprep_pair_recovery.sh"
PRODUCER = (
    ROOT
    / "case_studies/swiss_200m/scripts/produce_hicarprep_target_record_balfrin.sbatch"
)


def recover(forcing: Path, boundary: Path, *, job_id: str = "77"):
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; hicarprep_recover_pair "$2" "$3"',
            "test",
            str(HELPER),
            str(forcing),
            str(boundary),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "SLURM_JOB_ID": job_id},
    )


def test_complete_unmarked_pair_is_retained_for_validation(tmp_path) -> None:
    forcing = tmp_path / "forcing.nc"
    boundary = tmp_path / "forcing.lbc.nc"
    forcing.touch()
    boundary.touch()
    result = recover(forcing, boundary)
    assert result.returncode == 0
    assert result.stdout.strip() == "complete_unmarked"
    assert forcing.is_file() and boundary.is_file()
    assert not Path(f"{forcing}.ready").exists()
    assert not Path(f"{boundary}.ready").exists()


def test_single_unpublished_payload_is_quarantined_for_clean_retry(tmp_path) -> None:
    forcing = tmp_path / "forcing.nc"
    boundary = tmp_path / "forcing.lbc.nc"
    forcing.write_text("partial")
    result = recover(forcing, boundary)
    assert result.returncode == 0
    assert result.stdout.strip() == "clean"
    assert not forcing.exists() and not boundary.exists()
    quarantined = tmp_path / ".unpublished" / "forcing.77" / "forcing.nc"
    assert quarantined.read_text() == "partial"


def test_inconsistent_ready_markers_are_never_modified(tmp_path) -> None:
    forcing = tmp_path / "forcing.nc"
    boundary = tmp_path / "forcing.lbc.nc"
    forcing.touch()
    boundary.touch()
    forcing_ready = Path(f"{forcing}.ready")
    forcing_ready.touch()
    result = recover(forcing, boundary)
    assert result.returncode == 2
    assert forcing.is_file() and boundary.is_file() and forcing_ready.is_file()
    assert not Path(f"{boundary}.ready").exists()


def test_producer_validates_complete_unmarked_pair_before_publishing(tmp_path) -> None:
    forcing = tmp_path / "forcing.nc"
    boundary = tmp_path / "forcing.lbc.nc"
    static = tmp_path / "static.nc"
    forcing.touch()
    boundary.touch()
    static.touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "python.log"
    commands = {
        "module": "#!/bin/sh\nexit 0\n",
        "scontrol": "#!/bin/sh\necho 'PartitionName=pp-short AllowGroups=ALL'\n",
        "python": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n",
    }
    for name, source in commands.items():
        path = fake_bin / name
        path.write_text(source)
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(PRODUCER)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "REPO_ROOT": str(ROOT),
            "VALID_TIME": "2020-10-02T00:00:00",
            "HICAR_FORCING_OUTPUT": str(forcing),
            "HICAR_STATIC_DOMAIN": str(static),
            "HICARPREP_RBF_WEIGHTS": str(tmp_path / "unused-weights.nc"),
            "HICAR_PYTHON": "python",
            "SLURM_JOB_ID": "88",
        },
    )
    assert result.returncode == 0, result.stderr
    assert Path(f"{forcing}.ready").is_file()
    assert Path(f"{boundary}.ready").is_file()
    invocation = log.read_text()
    assert f"--forcing-file {forcing}" in invocation
    assert f"--boundary-file {boundary}" in invocation


def test_producer_reuses_ready_regular_forcing_without_boundary(tmp_path) -> None:
    forcing = tmp_path / "forcing.nc"
    static = tmp_path / "static.nc"
    forcing.touch()
    static.touch()
    Path(f"{forcing}.ready").touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "python.log"
    commands = {
        "module": "#!/bin/sh\nexit 0\n",
        "scontrol": "#!/bin/sh\necho 'PartitionName=pp-short AllowGroups=ALL'\n",
        "python": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log}\nexit 0\n",
    }
    for name, source in commands.items():
        path = fake_bin / name
        path.write_text(source)
        path.chmod(0o755)

    result = subprocess.run(
        ["bash", str(PRODUCER)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "REPO_ROOT": str(ROOT),
            "VALID_TIME": "2020-10-02T00:00:00",
            "HICAR_FORCING_OUTPUT": str(forcing),
            "HICAR_STATIC_DOMAIN": str(static),
            "HICARPREP_RBF_WEIGHTS": str(tmp_path / "unused-weights.nc"),
            "HICARPREP_WRITE_LBC": "0",
            "HICAR_PYTHON": "python",
            "SLURM_JOB_ID": "89",
        },
    )
    assert result.returncode == 0, result.stderr
    assert not Path(f"{forcing.with_suffix('.lbc.nc')}.ready").exists()
    invocation = log.read_text()
    assert f"--forcing-file {forcing}" in invocation
    assert "--boundary-file" not in invocation
