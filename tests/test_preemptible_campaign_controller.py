from __future__ import annotations

import importlib.util
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys
import stat

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


def publish_python_environment(report: Path, runtime_manifest: Path, requirements: Path) -> dict:
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
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
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
    forcing_cache_root = root / "forcing_cache"
    forcing_records_root = forcing_cache_root / "records"
    forcing_producer_root = forcing_cache_root / "producer"
    shared_records: dict[str, dict] = {}
    chains = []
    for chain_index in range(chain_count):
        chain_id = f"chain-{chain_index}"
        segment_root = root / "chains" / chain_id / "segment"
        forcing_publication = segment_root / "forcing_publication.json"
        if forcing_ready:
            publish_json(forcing_publication, {"status": "PASS"})
        records = []
        for record_index in range(record_count):
            valid_time = (datetime(2020, 1, 1) + timedelta(hours=record_index)).isoformat()
            forcing_file = (forcing_records_root / f"record-{record_index}.nc").resolve()
            records.append(
                {
                    "index": record_index,
                    "valid_time": valid_time,
                    "cycle_date": valid_time[:10].replace("-", ""),
                    "cycle_time": "0000",
                    "step_hours": record_index % 24,
                    "forcing_file": str(forcing_file),
                }
            )
            shared = shared_records.setdefault(
                str(forcing_file),
                {
                    "valid_time": valid_time,
                    "cycle_date": valid_time[:10].replace("-", ""),
                    "cycle_time": "0000",
                    "step_hours": record_index % 24,
                    "forcing_file": str(forcing_file),
                    "consumers": [],
                },
            )
            shared["consumers"].append(
                {
                    "chain_id": chain_id,
                    "segment_index": 0,
                    "segment_id": f"{chain_id}-segment",
                    "plan": str(segment_root / "chunk_plan.json"),
                    "forcing_publication": str(forcing_publication),
                }
            )
        chunk_plan = segment_root / "chunk_plan.json"
        chunk_plan.parent.mkdir(parents=True, exist_ok=True)
        chunk_plan.write_text(
            json.dumps(
                {
                    "records": records,
                    "chunk_root": str(segment_root),
                    "producer_root": str(forcing_producer_root),
                    "forcing_cache": {
                        "shared": True,
                        "records_root": str(forcing_records_root),
                        "producer_root": str(forcing_producer_root),
                    },
                }
            )
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
    cache_index = forcing_cache_root / "index.json"
    publish_json(
        cache_index,
        {
            "schema_version": 1,
            "status": "PLANNED",
            "campaign_id": "campaign-test",
            "shared": True,
            "records_root": str(forcing_records_root),
            "producer_root": str(forcing_producer_root),
            "static_file": str(tmp_path / "static.nc"),
            "record_count": len(shared_records),
            "records": [shared_records[path] for path in sorted(shared_records)],
        },
    )
    campaign = {
        "schema_version": 1,
        "status": "PLANNED",
        "campaign_id": "campaign-test",
        "purpose": "qualification",
        "campaign_root": str(root),
        "goal": {
            "outcome": "Exercise the next controller behavior under test.",
            "why_now": "The focused fixture isolates one orchestration behavior.",
            "evidence_needed": ["The expected controller state transition"],
            "stop_conditions": ["Stop after the expected transition"],
            "resource_rationale": "Use only the synthetic slots required by the fixture.",
        },
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
            "python_version": ".".join(str(item) for item in sys.version_info[:3]),
            "requirements_sha256": MODULE.sha256(requirements),
        },
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
            "model_node_budget": model_slots * 4,
            "model_slots": model_slots,
            "cpu_slots": 2,
            "max_cpu_batch_tasks": 32,
            "shared_forcing_cache": True,
            "input_task_weight": 3,
            "post_task_weight": 1,
            "prefetch_segments_per_chain": 1,
            "max_model_attempts": 5,
            "max_cpu_attempts": 3,
            "lease_seconds": 60,
            "rolling_retirement": True,
            "preserve_restart_every_segments": 30,
            "max_unretired_segments_per_chain": 2,
        },
        "forcing_cache": {
            "shared": True,
            "root": str(forcing_cache_root),
            "records_root": str(forcing_records_root),
            "producer_root": str(forcing_producer_root),
            "static_file": str(tmp_path / "static.nc"),
            "index": str(cache_index),
            "index_sha256": MODULE.sha256(cache_index),
        },
        "chains": chains,
        "controller": {
            "state": str(root / "controller_state.json"),
            "lease": str(root / "controller_state.lease"),
            "cpu_task_root": str(root / "cpu_tasks"),
        },
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


def test_controller_runs_multiple_independent_chains_without_a_go_artifact(tmp_path):
    path = make_campaign(tmp_path, chain_count=2, model_slots=2)
    scheduler = FakeSlurm()
    state, actions = reconcile(path, scheduler)
    assert state["goal"] == "Exercise the next controller behavior under test."
    assert len(state["chains"]) == 2
    assert actions


def test_model_uses_chain_static_and_shared_forcing_uses_base_grid(tmp_path):
    path = make_campaign(
        tmp_path,
        chain_count=1,
        model_slots=1,
        forcing_ready=False,
        record_count=1,
    )
    campaign = json.loads(path.read_text())
    segment = campaign["chains"][0]["segments"][0]
    chain_static = tmp_path / "chain-static.nc"
    segment["static_file"] = str(chain_static)
    attempt = {
        "attempt_id": "a001-test",
        "attempt_dir": str(tmp_path / "attempt"),
        "run_dir": str(tmp_path / "attempt/run"),
        "restart_dir": str(tmp_path / "attempt/restart"),
        "job_name": "test",
    }
    command = MODULE.model_submission_command(
        campaign,
        segment,
        attempt,
        None,
        Path(campaign["runtime_release"]["release_root"]),
    )
    export_argument = next(item for item in command if item.startswith("--export="))
    assert f"HICAR_STATIC_FILE={chain_static}" in export_argument

    state = MODULE.new_state(path, campaign)
    tasks = MODULE.pending_cpu_tasks(campaign, state)
    forcing = [task for task in tasks if task["kind"] == "forcing_record"]
    assert forcing
    assert {task["static_file"] for task in forcing} == {campaign["model"]["static_file"]}


def test_forcing_record_poll_uses_ready_contract_without_payload_rehash(tmp_path, monkeypatch):
    path = make_campaign(
        tmp_path,
        forcing_ready=False,
        record_count=1,
    )
    campaign = json.loads(path.read_text())
    segment = campaign["chains"][0]["segments"][0]
    plan_path = Path(segment["plan"])
    plan = json.loads(plan_path.read_text())
    record = plan["records"][0]
    record["valid_time"] = "2020-01-01T00:00:00"
    plan_path.write_text(json.dumps(plan))
    forcing = Path(record["forcing_file"])
    forcing.parent.mkdir(parents=True)
    forcing.write_bytes(b"published forcing")
    Path(f"{forcing}.ready").touch()
    forcing.with_suffix(".validation.json").write_text(json.dumps({"status": "PASS"}))
    forcing.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "valid_time": record["valid_time"],
                "forcing_sha256": "0" * 64,
            }
        )
    )
    monkeypatch.setattr(
        MODULE,
        "sha256",
        lambda _path: (_ for _ in ()).throw(AssertionError("payload re-hashed")),
    )
    task = {
        "kind": "forcing_record",
        "chain_id": "chain-0",
        "segment_index": 0,
        "record_index": 0,
    }
    assert MODULE.task_ready(task, campaign)


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
        Path(json.loads(campaign_path.read_text())["chains"][0]["segments"][0]["compressed_root"])
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
    assert {task["kind"] for task in state["cpu_batch"]["tasks"]} == {
        "segment_retirement",
        "restart_retirement",
    }
    execute_cpu_retirements(state)
    state, _actions = reconcile(campaign_path, scheduler)
    assert state["status"] == "COMPLETE"
    assert not source.exists()
    assert restart.exists()
    campaign_completion = Path(state["completion_report"])
    assert Path(f"{campaign_completion}.ready").is_file()
    payload = json.loads(campaign_completion.read_text())
    assert payload["status"] == "PASS"
    assert payload["goal"]["outcome"] == ("Exercise the next controller behavior under test.")
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
    active = sum(bool(chain["segments"][0]["attempts"]) for chain in state["chains"].values())
    assert active == 2
    assert sum("--nodes=4" in command for command in scheduler.commands) == 2


def test_empty_cluster_can_fill_all_eleven_four_node_slots_without_overqueue(
    tmp_path,
):
    campaign_path = make_campaign(tmp_path, chain_count=12, model_slots=11)
    scheduler = FakeSlurm()
    state, actions = reconcile(campaign_path, scheduler)
    model_actions = [action for action in actions if action["action"] == "SUBMIT_MODEL"]
    assert len(model_actions) == 11
    assert len(scheduler.commands) == 11
    assert all("--nodes=4" in command for command in scheduler.commands)

    for job_id in MODULE.job_ids(state):
        scheduler.records[job_id] = {"state": "PENDING", "exit_code": "0:0"}
    for _ in range(3):
        state, actions = reconcile(campaign_path, scheduler)
        assert not [action for action in actions if action["action"] == "SUBMIT_MODEL"]
    assert len(scheduler.commands) == 11
    assert (
        sum(
            len(segment["attempts"])
            for chain in state["chains"].values()
            for segment in chain["segments"]
        )
        == 11
    )


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
    assert cpu_actions[0]["task_count"] == 3
    assert state["cpu_batch"] is not None
    assert any("--array=0-2%2" == item for item in scheduler.commands[0])
    assert "--partition=pp-short" in scheduler.commands[0]
    assert "--cpus-per-task=4" in scheduler.commands[0]
    assert (
        len(
            {
                task["forcing_file"]
                for task in state["cpu_batch"]["tasks"]
                if task["kind"] == "forcing_record"
            }
        )
        == 3
    )


def test_ready_forcing_does_not_release_a_still_running_producer(tmp_path):
    campaign_path = make_campaign(
        tmp_path,
        forcing_ready=False,
        record_count=1,
    )
    campaign = json.loads(campaign_path.read_text())
    scheduler = FakeSlurm()
    state, _actions = reconcile(campaign_path, scheduler)
    original_batch = state["cpu_batch"]
    original_job = original_batch["job_id"]

    plan = json.loads(Path(campaign["chains"][0]["segments"][0]["plan"]).read_text())
    record = plan["records"][0]
    forcing = Path(record["forcing_file"])
    forcing.parent.mkdir(parents=True)
    forcing.write_bytes(b"published while producer is still running")
    Path(f"{forcing}.ready").touch()
    forcing.with_suffix(".validation.json").write_text(json.dumps({"status": "PASS"}))
    forcing.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "valid_time": record["valid_time"],
                "forcing_sha256": hashlib.sha256(forcing.read_bytes()).hexdigest(),
            }
        )
    )

    scheduler.records[original_job] = {"state": "RUNNING", "exit_code": "0:0"}
    state, actions = reconcile(campaign_path, scheduler)

    assert state["cpu_batch"]["batch_id"] == original_batch["batch_id"]
    assert state["cpu_batch"]["status"] == "RUNNING"
    assert not actions
    assert state["events"][-1]["kind"] != "CPU_BATCH_PUBLISHED"

    scheduler.records[original_job] = {"state": "COMPLETED", "exit_code": "0:0"}
    state, actions = reconcile(campaign_path, scheduler)

    assert any(event["kind"] == "CPU_BATCH_PUBLISHED" for event in state["events"])
    assert state["cpu_batch"] is not None
    assert {task["kind"] for task in state["cpu_batch"]["tasks"]} == {"forcing_finalize"}
    assert [action["action"] for action in actions] == ["SUBMIT_CPU_BATCH"]


def test_forcing_batch_is_bounded_to_release_segment_finalizers(tmp_path):
    campaign_path = make_campaign(
        tmp_path,
        chain_count=2,
        model_slots=2,
        forcing_ready=False,
        record_count=40,
    )
    scheduler = FakeSlurm()
    state, actions = reconcile(campaign_path, scheduler)
    cpu_actions = [action for action in actions if action["action"] == "SUBMIT_CPU_BATCH"]
    assert len(cpu_actions) == 1
    assert cpu_actions[0]["task_count"] == 32
    assert len(state["cpu_batch"]["tasks"]) == 32
    assert any("--array=0-31%2" == item for item in scheduler.commands[0])


def test_input_and_post_tasks_share_the_head_of_the_cpu_array(tmp_path):
    campaign_path = make_campaign(
        tmp_path,
        forcing_ready=False,
        record_count=4,
    )
    campaign = json.loads(campaign_path.read_text())
    state = MODULE.new_state(campaign_path, campaign)
    runtime = state["chains"]["chain-0"]["segments"][0]
    run_dir = tmp_path / "completed-run"
    completion = run_dir / "model_chunk_completion.json"
    publish_json(completion, {"status": "PASS", "output": {"files": []}})
    runtime["model_completion"] = str(completion)
    runtime["attempts"].append(
        {
            "attempt_id": "a001-complete",
            "run_dir": str(run_dir),
            "status": "PUBLISHED",
        }
    )

    tasks = MODULE.pending_cpu_tasks(campaign, state)
    assert [task["kind"] for task in tasks[:2]] == [
        "forcing_record",
        "solver_audit",
    ]
    assert sum(task["kind"] == "forcing_record" for task in tasks) == 4


def test_failed_cpu_batch_retries_with_more_memory_proxy_cores(tmp_path):
    campaign_path = make_campaign(
        tmp_path,
        forcing_ready=False,
        record_count=1,
    )
    scheduler = FakeSlurm()
    state, _actions = reconcile(campaign_path, scheduler)
    first_job = state["cpu_batch"]["job_id"]
    assert "--cpus-per-task=4" in scheduler.commands[-1]

    scheduler.records[first_job] = {
        "state": "OUT_OF_MEMORY",
        "exit_code": "137:9",
    }
    state, _actions = reconcile(campaign_path, scheduler)
    assert state["cpu_batch"] is not None
    assert state["cpu_batch"]["cpus_per_task"] == 8
    assert "--cpus-per-task=8" in scheduler.commands[-1]
    assert all(
        segment["terminal_error"] is None
        for chain in state["chains"].values()
        for segment in chain["segments"]
    )


def test_published_lifecycle_task_clears_exhausted_retry_error(tmp_path):
    campaign_path = make_campaign(tmp_path)
    campaign = json.loads(campaign_path.read_text())
    state = MODULE.new_state(campaign_path, campaign)
    runtime = state["chains"]["chain-0"]["segments"][0]
    runtime["terminal_error"] = "CPU task segment_retirement failed in scheduler state FAILED"
    runtime["cpu_failures"]["segment_retirement"] = 3

    MODULE.clear_resolved_lifecycle_terminal_errors(state)
    assert runtime["terminal_error"] is not None

    runtime["segment_retirement"] = str(tmp_path / "segment_retirement.json")
    MODULE.clear_resolved_lifecycle_terminal_errors(state)

    assert runtime["terminal_error"] is None
    assert runtime["cpu_failures"]["segment_retirement"] == 3
    assert state["events"][-1] == {
        "kind": "CPU_TASK_RECOVERED",
        "time": state["events"][-1]["time"],
        "chain_id": "chain-0",
        "segment_id": "chain-0-segment",
        "task_kind": "segment_retirement",
        "failure_count": 3,
    }


def test_shared_forcing_waits_for_every_consumer_then_retires_once(tmp_path):
    campaign_path = make_campaign(
        tmp_path,
        chain_count=2,
        model_slots=2,
        forcing_ready=False,
        record_count=1,
    )
    campaign = json.loads(campaign_path.read_text())
    state = MODULE.new_state(campaign_path, campaign)
    cache_index = json.loads(Path(campaign["forcing_cache"]["index"]).read_text())
    record = cache_index["records"][0]
    forcing = Path(record["forcing_file"])
    forcing.parent.mkdir(parents=True)
    forcing.write_bytes(b"shared forcing")
    digest = hashlib.sha256(forcing.read_bytes()).hexdigest()
    Path(f"{forcing}.ready").touch()
    forcing.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "valid_time": record["valid_time"],
                "forcing_sha256": digest,
            }
        )
    )
    forcing.with_suffix(".validation.json").write_text(json.dumps({"status": "PASS"}))
    producer_cache = (
        Path(campaign["forcing_cache"]["producer_root"]) / "cache" / record["cycle_date"]
    )
    producer_cache.mkdir(parents=True)
    (producer_cache / "cycle.grib").write_bytes(b"cycle cache")

    for consumer in record["consumers"]:
        publication = Path(consumer["forcing_publication"])
        publish_json(
            publication,
            {
                "status": "PASS",
                "entries": [
                    {
                        "forcing_file": str(forcing.resolve()),
                        "forcing_sha256": digest,
                    }
                ],
            },
        )

    first = record["consumers"][0]
    first_segment = campaign["chains"][0]["segments"][0]
    first_report = (
        Path(first_segment["attempt_root"]).parent / "lifecycle" / "segment_retirement.json"
    )
    publish_json(first_report, {"status": "PASS", "action": "RETIRED"})
    state["chains"][first["chain_id"]]["segments"][0]["segment_retirement"] = str(first_report)
    assert MODULE.pending_forcing_cache_retirements(campaign, state) == []
    assert forcing.is_file()

    second = record["consumers"][1]
    second_segment = campaign["chains"][1]["segments"][0]
    second_report = (
        Path(second_segment["attempt_root"]).parent / "lifecycle" / "segment_retirement.json"
    )
    publish_json(second_report, {"status": "PASS", "action": "RETIRED"})
    state["chains"][second["chain_id"]]["segments"][0]["segment_retirement"] = str(second_report)
    tasks = MODULE.pending_forcing_cache_retirements(campaign, state)
    assert [task["kind"] for task in tasks] == ["forcing_cache_retirement"]

    def execute_retirement(task: dict) -> None:
        task_file = tmp_path / f"{task['kind']}.json"
        payload = {
            "schema_version": 1,
            "campaign_id": campaign["campaign_id"],
            "batch_id": task["kind"],
            "tasks": [task],
        }
        task_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        expected = hashlib.sha256(task_file.read_bytes()).hexdigest()
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "orchestration/retire_campaign_artifacts.py"),
                "--task-file",
                str(task_file),
                "--expected-sha256",
                expected,
                "--index",
                "0",
            ],
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr + result.stdout

    execute_retirement(tasks[0])
    assert not forcing.exists()
    cycle_tasks = MODULE.pending_forcing_cache_retirements(campaign, state)
    assert [task["kind"] for task in cycle_tasks] == ["forcing_cycle_cache_retirement"]
    execute_retirement(cycle_tasks[0])
    assert not (producer_cache / "cycle.grib").exists()
    assert MODULE.forcing_cache_retirement_complete(campaign)


def test_slurm_query_aggregates_numeric_balfrin_array_elements(monkeypatch):
    outputs = {
        "123": "5001|COMPLETED|0:0|\n5002|RUNNING|0:0|\n",
        "124": "5003|COMPLETED|0:0|\n5004|PREEMPTED|0:15|\n",
    }
    queried = []

    def run(arguments, **_kwargs):
        parent = arguments[arguments.index("-j") + 1]
        queried.append(parent)
        return type("Result", (), {"stdout": outputs[parent]})()

    monkeypatch.setattr(MODULE.subprocess, "run", run)
    states = MODULE.Slurm().query(["123", "124"])
    assert queried == ["123", "124"]
    assert states["123"]["state"] == "RUNNING"
    assert states["124"]["state"] == "PREEMPTED"


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
