from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_every_slurm_script_names_one_reviewed_partition() -> None:
    allowed = {"debug", "short", "normal", "preemptible", "pp-short", "pp-long"}
    failures = []
    for path in ROOT.rglob("*.sbatch"):
        if ".git" in path.parts or "HICAR" in path.parts:
            continue
        partitions = [
            line.split("=", 1)[1].strip()
            for line in path.read_text(errors="replace").splitlines()
            if line.strip().startswith("#SBATCH --partition=")
        ]
        if len(partitions) != 1 or partitions[0] not in allowed:
            failures.append((str(path.relative_to(ROOT)), partitions))
    assert failures == []


def test_submission_scripts_check_exact_s83_or_all() -> None:
    for name in ("produce_hicarprep_target_record_balfrin.sbatch", "run_rea_l_stream_chunk_balfrin.sbatch"):
        text = (ROOT / "case_studies/swiss_200m/scripts" / name).read_text()
        assert "AllowGroups=ALL" in text
        assert "AllowGroups=s83" in text
        assert "s83opr" not in text and "s83disp" not in text
