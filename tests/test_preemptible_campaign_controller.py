from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import stat

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "orchestration/preemptible_campaign.py"
SPEC = importlib.util.spec_from_file_location("preemptible_campaign", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
RELEASE_SPEC = importlib.util.spec_from_file_location(
    "prepare_runtime_release",
    ROOT / "orchestration/prepare_runtime_release.py",
)
RELEASE_MODULE = importlib.util.module_from_spec(RELEASE_SPEC)
sys.modules[RELEASE_SPEC.name] = RELEASE_MODULE
RELEASE_SPEC.loader.exec_module(RELEASE_MODULE)


class FakeSlurm:
    def __init__(self):
        self.records: dict[str, dict[str, str]] = {}
        self.commands: list[list[str]] = []

    def query(self, job_ids: list[str]) -> dict[str, dict[str, str]]:
        return {job_id: self.records[job_id] for job_id in job_ids if job_id in self.records}

    def find_job(self, _job_name: str) -> list[str]:
        return []

    def submit(self, arguments: list[str]) -> str:
        self.commands.append(arguments)
        return str(1000 + len(self.commands))


def publish_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    Path(f"{path}.ready").touch()


def publish_python_environment(
    report: Path, runtime_manifest: Path, requirements: Path
) -> dict:
    environment_root = report.with_suffix(".venv")
    executable = environment_root / "bin/python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    freeze = sorted(
        line.strip()
        for line in subprocess.check_output(
            [str(executable), "-m", "pip", "freeze"], text=True
        ).splitlines()
        if line.strip()
    )
    for path in (executable.parent, environment_root):
        path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    freeze_bytes = ("\n".join(freeze) + "\n").encode()
    payload = {
        "schema_version": 2,
        "status": "PASS",
        "purpose": "preemptible-runtime",
        "environment_root": str(environment_root),
        "immutable": True,
        "python": str(executable),
        "python_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "python_version": ".".join(
            str(item) for item in sys.version_info[:3]
        ),
        "runtime_release": str(runtime_manifest),
        "runtime_release_sha256": MODULE.sha256(runtime_manifest),
        "requirements": str(requirements),
        "requirements_sha256": MODULE.sha256(requirements),
        "versions": {},
        "pip_freeze": freeze,
        "pip_freeze_sha256": hashlib.sha256(freeze_bytes).hexdigest(),
    }
    publish_json(report, payload)
    return payload


def make_campaign(
    tmp_path: Path,
    *,
    chain_count: int = 1,
    model_slots: int = 1,
    forcing_ready: bool = True,
    record_count: int = 0,
) -> Path:
    root = tmp_path / "campaign"
    release_root = tmp_path / "runtime-release"
    release = RELEASE_MODULE.build_release(ROOT, release_root, "engineering")
    release_manifest = release_root / "runtime_release.json"
    requirements = release_root / "requirements/balfrin-preemptible.txt"
    python_report = tmp_path / "python_environment.json"
    python_payload = publish_python_environment(
        python_report,
        release_manifest,
        requirements,
    )
    chains = []
    for chain_index in range(chain_count):
        chain_id = f"chain-{chain_index}"
        segment_root = root / "chains" / chain_id / "segment"
        forcing_publication = segment_root / "forcing_publication.json"
        if forcing_ready:
            publish_json(forcing_publication, {"status": "PASS"})
        records = []
        for record_index in range(record_count):
            records.append(
                {
                    "forcing_file": str(
                        segment_root / "forcing" / f"record-{record_index}.nc"
                    )
                }
            )
        chunk_plan = segment_root / "chunk_plan.json"
        chunk_plan.parent.mkdir(parents=True, exist_ok=True)
        chunk_plan.write_text(
            json.dumps({"records": records, "chunk_root": str(segment_root)})
        )
        chains.append(
            {
                "chain_id": chain_id,
                "segments": [
                    {
                        "sequence": 1,
                        "segment_id": f"{chain_id}-segment",
                        "start": "2020-01-01T00:00:00",
                        "end": "2020-01-01T01:00:00",
                        "hours": 1,
                        "plan": str(chunk_plan),
                        "forcing_publication": str(forcing_publication),
                        "attempt_root": str(segment_root / "attempts"),
                        "compressed_root": str(segment_root / "compressed"),
                        "rea_l_land_initialization": True,
                    }
                ],
            }
        )
    campaign = {
        "schema_version": 1,
        "status": "PLANNED",
        "campaign_id": "campaign-test",
        "purpose": "qualification",
        "campaign_root": str(root),
        "runtime_release": {
            "path": str(release_manifest),
            "sha256": MODULE.sha256(release_manifest),
            "release_root": str(release_root),
            "purpose": release["purpose"],
            "source_commit": release["source_commit"],
            "source_dirty": release["source_dirty"],
        },
        "python_environment": {
            "path": str(python_report),
            "sha256": MODULE.sha256(python_report),
            "python": python_payload["python"],
            "python_version": ".".join(
                str(item) for item in sys.version_info[:3]
            ),
            "requirements_sha256": MODULE.sha256(requirements),
        },
        "independent_chain_authorization": None,
        "production_authorization": None,
        "model": {
            "partition": "preemptible",
            "nodes": 4,
            "time_limit": "06:00:00",
            "case_root": str(tmp_path / "case"),
            "hicar_root": str(tmp_path / "HICAR"),
            "static_file": str(tmp_path / "static.nc"),
            "expected_hicar_commit": "a" * 40,
            "output_interval_seconds": 3600,
            "output_profile": "routine",
        },
        "policy": {
            "segment_hours": 1,
            "model_node_budget": 44,
            "model_slots": model_slots,
            "cpu_slots": 2,
            "prefetch_segments_per_chain": 1,
            "max_model_attempts": 5,
            "max_cpu_attempts": 3,
            "lease_seconds": 60,
            "rolling_retirement": True,
            "preserve_restart_every_segments": 30,
            "max_unretired_segments_per_chain": 2,
        },
        "chains": chains,
        "controller": {
            "state": str(root / "controller_state.json"),
            "lease": str(root / "controller_state.lease"),
            "cpu_task_root": str(root / "cpu_tasks"),
        },
    }
    if chain_count > 1:
        authorization_path = tmp_path / "independent_authorization.json"
        scope = MODULE.independent_chain_scope(campaign)
        authorization = {
            "schema_version": 1,
            "status": "PASS",
            "decision": "GO_INDEPENDENT_CHAINS",
            "scope": scope,
            "scope_sha256": MODULE.json_payload_sha256(scope),
        }
        publish_json(authorization_path, authorization)
        campaign["independent_chain_authorization"] = {
            "path": str(authorization_path),
            "sha256": MODULE.sha256(authorization_path),
            "decision": authorization["decision"],
            "scope": scope,
            "scope_sha256": authorization["scope_sha256"],
        }
    path = tmp_path / "campaign.json"
    publish_json(path, campaign)
    return path


def reconcile(path: Path, scheduler: FakeSlurm):
    campaign = json.loads(path.read_text())
    return MODULE.reconcile(
        campaign_path=path,
        repo_root=Path(campaign["runtime_release"]["release_root"]),
        scheduler=scheduler,
        execute=True,
    )


def test_controller_rejects_authorization_for_another_chain_scope(tmp_path):
    path = make_campaign(tmp_path, chain_count=2, model_slots=2)
    campaign = json.loads(path.read_text())
    campaign["chains"][1]["segments"][0]["end"] = "2020-01-01T02:00:00"
    path.write_text(json.dumps(campaign))
    scheduler = FakeSlurm()
    with pytest.raises(ValueError, match="no longer matches campaign"):
        reconcile(path, scheduler)


def execute_cpu_retirements(state: dict) -> None:
    batch = state["cpu_batch"]
    assert batch is not None
    for index, task in enumerate(batch["tasks"]):
        assert task["kind"] in {"segment_retirement", "restart_retirement"}
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "orchestration/retire_campaign_artifacts.py"),
                "--task-file",
                batch["task_file"],
                "--expected-sha256",
                batch["task_sha256"],
                "--index",
                str(index),
            ],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr + result.stdout


def test_preempted_model_retries_in_a_new_attempt_and_publishes_exact_union(tmp_path):
    campaign_path = make_campaign(tmp_path)
    scheduler = FakeSlurm()

    state, _actions = reconcile(campaign_path, scheduler)
    first = state["chains"]["chain-0"]["segments"][0]["attempts"][0]
    assert first["job_id"] == "1001"
    assert "--partition=preemptible" in first["command"]
    assert "--no-requeue" in first["command"]
    assert "--signal=B:USR1@300" in first["command"]

    scheduler.records["1001"] = {"state": "PREEMPTED", "exit_code": "0:15"}
    state, _actions = reconcile(campaign_path, scheduler)
    attempts = state["chains"]["chain-0"]["segments"][0]["attempts"]
    assert len(attempts) == 2
    assert attempts[0]["status"] == "RETRYABLE"
    assert attempts[0]["attempt_dir"] != attempts[1]["attempt_dir"]
    assert MODULE.job_ids(state) == ["1002"]

    latest = attempts[-1]
    run_dir = Path(latest["run_dir"])
    run_dir.mkdir(parents=True)
    source = run_dir / "output.nc"
    source.write_bytes(b"model output")
    restart = run_dir / "restart.nc"
    restart.write_bytes(b"restart")
    completion = run_dir / "model_chunk_completion.json"
    publish_json(
        completion,
        {
            "status": "PASS",
            "start": "2020-01-01T00:00:00",
            "end": "2020-01-01T01:00:00",
            "output": {
                "times": [
                    "2020-01-01T00:00:00",
                    "2020-01-01T01:00:00",
                ],
                "files": [
                    {
                        "path": str(source),
                        "size_bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
            },
            "restart": {
                "path": str(restart),
                "sha256": hashlib.sha256(restart.read_bytes()).hexdigest(),
            },
        },
    )
    solver = run_dir / "scientific_validation/solver_log_diagnostics.json"
    publish_json(solver, {"status": "PASS"})
    target = (
        Path(
            json.loads(campaign_path.read_text())["chains"][0]["segments"][0][
                "compressed_root"
            ]
        )
        / latest["attempt_id"]
        / source.name
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    Path(f"{target}.ready").touch()
    publish_json(
        Path(f"{target}.compression.json"),
        {
            "status": "PASS",
            "source": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "target": str(target.resolve()),
            "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        },
    )

    state, _actions = reconcile(campaign_path, scheduler)
    assert state["cpu_batch"]["tasks"][0]["kind"] == "segment_retirement"
    execute_cpu_retirements(state)
    state, _actions = reconcile(campaign_path, scheduler)
    assert state["cpu_batch"]["tasks"][0]["kind"] == "restart_retirement"
    execute_cpu_retirements(state)
    state, _actions = reconcile(campaign_path, scheduler)
    assert state["status"] == "COMPLETE"
    assert not source.exists()
    assert restart.exists()
    campaign_completion = Path(state["completion_report"])
    assert Path(f"{campaign_completion}.ready").is_file()
    payload = json.loads(campaign_completion.read_text())
    assert payload["status"] == "PASS"
    assert payload["chains"][0]["output_count"] == 2


def test_unlimited_scheduler_retries_remain_one_pending_attempt(tmp_path):
    campaign_path = make_campaign(tmp_path)
    campaign = json.loads(campaign_path.read_text())
    campaign["policy"]["max_model_attempts"] = 0
    campaign_path.write_text(json.dumps(campaign))
    scheduler = FakeSlurm()

    for sequence in range(7):
        state, _actions = reconcile(campaign_path, scheduler)
        attempts = state["chains"]["chain-0"]["segments"][0]["attempts"]
        assert len(attempts) == sequence + 1
        assert MODULE.job_ids(state) == [attempts[-1]["job_id"]]
        scheduler.records[attempts[-1]["job_id"]] = {
            "state": "PREEMPTED",
            "exit_code": "0:15",
        }

    state, _actions = reconcile(campaign_path, scheduler)
    attempts = state["chains"]["chain-0"]["segments"][0]["attempts"]
    assert len(attempts) == 8
    assert not state["blockers"]
    assert MODULE.job_ids(state) == [attempts[-1]["job_id"]]


def test_model_concurrency_is_bounded_globally_across_chains(tmp_path):
    campaign_path = make_campaign(tmp_path, chain_count=3, model_slots=2)
    scheduler = FakeSlurm()
    state, actions = reconcile(campaign_path, scheduler)
    assert len([action for action in actions if action["action"] == "SUBMIT_MODEL"]) == 2
    active = sum(
        bool(chain["segments"][0]["attempts"])
        for chain in state["chains"].values()
    )
    assert active == 2
    assert sum("--nodes=4" in command for command in scheduler.commands) == 2


def test_empty_cluster_can_fill_all_eleven_four_node_slots_without_overqueue(
    tmp_path,
):
    campaign_path = make_campaign(tmp_path, chain_count=12, model_slots=11)
    scheduler = FakeSlurm()
    state, actions = reconcile(campaign_path, scheduler)
    model_actions = [
        action for action in actions if action["action"] == "SUBMIT_MODEL"
    ]
    assert len(model_actions) == 11
    assert len(scheduler.commands) == 11
    assert all("--nodes=4" in command for command in scheduler.commands)

    for job_id in MODULE.job_ids(state):
        scheduler.records[job_id] = {"state": "PENDING", "exit_code": "0:0"}
    for _ in range(3):
        state, actions = reconcile(campaign_path, scheduler)
        assert not [
            action for action in actions if action["action"] == "SUBMIT_MODEL"
        ]
    assert len(scheduler.commands) == 11
    assert sum(
        len(segment["attempts"])
        for chain in state["chains"].values()
        for segment in chain["segments"]
    ) == 11


def test_forcing_uses_one_globally_throttled_cpu_array(tmp_path):
    campaign_path = make_campaign(
        tmp_path,
        chain_count=2,
        model_slots=2,
        forcing_ready=False,
        record_count=3,
    )
    scheduler = FakeSlurm()
    state, actions = reconcile(campaign_path, scheduler)
    cpu_actions = [action for action in actions if action["action"] == "SUBMIT_CPU_BATCH"]
    assert len(cpu_actions) == 1
    assert cpu_actions[0]["task_count"] == 6
    assert state["cpu_batch"] is not None
    assert any("--array=0-5%2" == item for item in scheduler.commands[0])


def test_slurm_query_aggregates_array_elements(monkeypatch):
    class Result:
        stdout = (
            "123_0|COMPLETED|0:0|\n"
            "123_1|PREEMPTED|0:15|\n"
            "124_0|COMPLETED|0:0|\n"
            "124_1|COMPLETED|0:0|\n"
        )

    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_args, **_kwargs: Result())
    states = MODULE.Slurm().query(["123", "124"])
    assert states["123"]["state"] == "PREEMPTED"
    assert states["124"]["state"] == "COMPLETED"


def test_hard_kill_exit_signal_is_retryable_without_a_signal_report(tmp_path):
    attempt = {"run_dir": str(tmp_path)}
    assert (
        MODULE.classify_attempt_terminal(
            attempt,
            {"state": "FAILED", "exit_code": "0:9"},
        )
        == "RETRYABLE"
    )
    assert (
        MODULE.classify_attempt_terminal(
            attempt,
            {"state": "FAILED", "exit_code": "1:0"},
        )
        == "TERMINAL_FAILURE"
    )


def test_zero_capacity_pauses_new_submissions(tmp_path):
    campaign_path = make_campaign(tmp_path)
    state = MODULE.set_capacity(campaign_path, models=0, cpus=0)
    assert state["capacity"] == {"model_slots": 0, "cpu_slots": 0}
    scheduler = FakeSlurm()
    _state, actions = reconcile(campaign_path, scheduler)
    assert actions == []
    assert scheduler.commands == []
