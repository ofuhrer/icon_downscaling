from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "runtime_contract_partition_policy",
    ROOT / "orchestration/runtime_contract.py",
)
POLICY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = POLICY
SPEC.loader.exec_module(POLICY)


def record(partition: str, groups: str, state: str = "UP") -> str:
    return (
        f"PartitionName={partition} AllowGroups={groups} "
        f"AllowAccounts=ALL State={state} Nodes=nid[001000-001001]"
    )


def test_exact_s83_and_all_are_accepted():
    assert POLICY.validate_s83_partition_record(
        "preemptible", record("preemptible", "s83")
    )["AllowGroups"] == "s83"
    assert POLICY.validate_s83_partition_record(
        "pp-short", record("pp-short", "ALL")
    )["AllowGroups"] == "ALL"


@pytest.mark.parametrize("groups", ["s83opr", "s83disp", "s83opr,s83disp"])
def test_supplemental_groups_never_authorize_submission(groups):
    with pytest.raises(ValueError, match="not open to exact group s83"):
        POLICY.validate_s83_partition_record(
            "pp-short", record("pp-short", groups)
        )


def test_unreviewed_or_down_partition_is_rejected():
    with pytest.raises(ValueError, match="reviewed s83 allowlist"):
        POLICY.validate_s83_partition_record(
            "pp-production", record("pp-production", "s83opr")
        )
    with pytest.raises(ValueError, match="is not UP"):
        POLICY.validate_s83_partition_record(
            "pp-short", record("pp-short", "ALL", "DOWN")
        )


def test_every_programmatic_submission_requires_one_explicit_partition():
    assert (
        POLICY.explicit_sbatch_partition(
            ["sbatch", "--partition=pp-short", "job.sbatch"]
        )
        == "pp-short"
    )
    with pytest.raises(ValueError, match="exactly one explicit partition"):
        POLICY.explicit_sbatch_partition(["sbatch", "job.sbatch"])
    with pytest.raises(ValueError, match="exactly one explicit partition"):
        POLICY.explicit_sbatch_partition(
            ["sbatch", "--partition=pp-short,pp-long", "job.sbatch"]
        )


def test_checked_in_sbatch_directives_use_only_reviewed_s83_partitions():
    excluded = {".git", "HICAR", "fieldextra", "archives", "tmp"}
    violations = []
    for path in ROOT.rglob("*.sbatch"):
        if excluded.intersection(path.relative_to(ROOT).parts):
            continue
        selected = []
        for line in path.read_text(errors="replace").splitlines():
            stripped = line.strip()
            if not stripped.startswith("#SBATCH"):
                continue
            tokens = stripped.split()
            for index, token in enumerate(tokens):
                if token.startswith("--partition="):
                    partition = token.split("=", 1)[1]
                elif token in {"--partition", "-p"} and index + 1 < len(tokens):
                    partition = tokens[index + 1]
                else:
                    continue
                selected.append(partition)
                if partition not in POLICY.S83_APPROVED_PARTITIONS:
                    violations.append(f"{path.relative_to(ROOT)}: {partition}")
        if len(selected) != 1:
            violations.append(
                f"{path.relative_to(ROOT)}: expected one partition, "
                f"found {selected}"
            )
    assert violations == []
