from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path

import pytest

from orchestration.cleanup_campaign_payloads import (
    ActiveJob,
    CleanupSafetyError,
    apply_plan,
    build_plan,
)
from orchestration.rd_campaign import Campaign, hours, segments, stamp
from scripts.restart_transition_provenance import (
    publish_receipt,
    receipt_path,
    validate_receipt,
)


def configured_campaign(tmp_path: Path, *, full_season_input_lists: bool = False) -> Campaign:
    config = {
        "root": str(tmp_path / "campaign"),
        "repo_root": str(tmp_path / "source"),
        "forcing_dir": str(tmp_path / "forcing"),
        "python": "python3",
        "hicar_executable": "HICAR_gpu",
        "hicar_support_dir": "run",
        "hicar_build_provenance": "build.txt",
        "rbf_weights": "weights.nc",
        "full_season_input_lists": full_season_input_lists,
        "segment_hours": 1,
        "use_sparse_lbc": False,
        "seasons": [
            {
                "name": "winter",
                "start": "2020-01-01T00:00:00",
                "end": "2020-01-01T04:00:00",
                "static": str(tmp_path / "static.nc"),
                "static_sha256": "a" * 64,
            }
        ],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    campaign = Campaign(path)
    campaign.root.mkdir(parents=True)
    (campaign.root / "campaign.json").write_text(
        json.dumps({"coordinator_source": {"commit": "c" * 40}}), encoding="utf-8"
    )
    return campaign


def write_manifest(campaign: Campaign, when: datetime, payload: bytes = b"forcing") -> Path:
    season = campaign.seasons[0]
    forcing, _ = campaign.paths(season, when)
    forcing.parent.mkdir(parents=True, exist_ok=True)
    forcing.write_bytes(payload)
    Path(f"{forcing}.ready").touch()
    digest = "b" * 64
    manifest = Path(f"{forcing}.hicarprep-manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "schema": "hicarprep-target-forcing-manifest-v1",
                "status": "PASS",
                "valid_time": when.strftime("%Y-%m-%dT%H:%M:%S"),
                "forcing_file": str(forcing),
                "forcing_sha256": digest,
                "output": {"path": str(forcing), "sha256": digest},
                "static": {"path": str(season.static), "sha256": "a" * 64},
            }
        ),
        encoding="utf-8",
    )
    return forcing


def complete_segment(campaign: Campaign, index: int) -> Path:
    season = campaign.seasons[0]
    start, end = list(segments(season.start, season.end, campaign.segment_hours))[index]
    root = campaign.root / season.name / f"{index:03d}_{stamp(start)}_{stamp(end)}"
    attempt = root / "attempt-1"
    attempt.mkdir(parents=True)
    payload = {
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "forcing_records": len(list(hours(start, end))),
        "restart": str(attempt / "restart" / f"checkpoint-{stamp(end)}.nc"),
        "restart_time": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (attempt / "segment.json").write_text(json.dumps(payload), encoding="utf-8")
    (attempt / "segment_validation.json").write_text(json.dumps(payload), encoding="utf-8")
    (attempt / "segment.complete").touch()
    terminal = Path(payload["restart"])
    terminal.parent.mkdir(parents=True)
    terminal.write_bytes(f"restart-{index}".encode())
    if index:
        previous_start, previous_end = list(
            segments(season.start, season.end, campaign.segment_hours)
        )[index - 1]
        previous_root = (
            campaign.root
            / season.name
            / f"{index - 1:03d}_{stamp(previous_start)}_{stamp(previous_end)}"
        )
        previous = campaign.completed_attempt(previous_root)
        assert previous is not None
        previous_report = json.loads((previous / "segment.json").read_text(encoding="utf-8"))
        previous_terminal = Path(previous_report["restart"])
        (attempt / "restart" / previous_terminal.name).symlink_to(previous_terminal)
        publish_receipt(
            previous,
            attempt,
            season=season.name,
            predecessor_index=index - 1,
            successor_index=index,
            campaign_commit="c" * 40,
            attestor_commit="d" * 40,
        )
    return attempt


def populate(campaign: Campaign) -> list[Path]:
    season = campaign.seasons[0]
    return [write_manifest(campaign, when) for when in hours(season.start, season.end)]


def test_plan_prunes_only_strictly_before_first_incomplete_start(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    complete_segment(campaign, 1)

    plan = build_plan(campaign, [])

    forcing_targets = [item for item in plan["targets"] if item["kind"] == "forcing"]
    assert [Path(item["path"]) for item in forcing_targets] == forcing[:2]
    assert plan["summary"]["blocked_count"] == 0
    assert plan["summary"]["target_count"] == 3
    assert plan["frontiers"][0]["first_incomplete_start"] == "2020-01-01T02:00:00"
    # The shared 02:00 endpoint belongs to both the completed predecessor and
    # the next incomplete segment, so it is deliberately retained.
    assert forcing[2].is_file() and Path(f"{forcing[2]}.ready").is_file()


def test_completed_season_can_prune_its_terminal_forcing_endpoint(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    for index in range(4):
        complete_segment(campaign, index)

    plan = build_plan(campaign, [])

    assert [
        Path(item["path"]) for item in plan["targets"] if item["kind"] == "forcing"
    ] == forcing
    assert plan["frontiers"][0]["first_incomplete_start"] is None


def test_live_model_reference_blocks_even_a_past_record(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    attempt = complete_segment(campaign, 0)
    complete_segment(campaign, 1)
    (attempt.parent / "forcing.txt").write_text(f'"{forcing[0]}"\n', encoding="utf-8")
    (attempt.parent / "attempt-1.job").write_text("123\n", encoding="utf-8")

    plan = build_plan(campaign, [ActiveJob("123", "COMPLETING", "hc-win-000-a1")])

    assert [
        item["path"] for item in plan["targets"] if item["kind"] == "forcing"
    ] == [str(forcing[1])]
    assert plan["blockers"] == [
        {"path": str(forcing[0]), "reason": "referenced_by_live_model"}
    ]


def test_live_input_producer_target_blocks_cleanup(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    complete_segment(campaign, 1)
    job = campaign.root / "input_jobs/winter/20200101_0100/attempt-1.job"
    job.parent.mkdir(parents=True)
    job.write_text("234\n", encoding="utf-8")

    plan = build_plan(campaign, [ActiveJob("234", "RUNNING", "hp-010101")])

    assert [
        item["path"] for item in plan["targets"] if item["kind"] == "forcing"
    ] == [str(forcing[0])]
    assert plan["blockers"] == [
        {"path": str(forcing[1]), "reason": "target_of_live_producer"}
    ]


def test_unmapped_campaign_like_job_fails_closed(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    populate(campaign)

    with pytest.raises(CleanupSafetyError, match="has no job record"):
        build_plan(campaign, [ActiveJob("999", "PENDING", "hc-win-003-a1")])


def test_noncontiguous_complete_chain_fails_closed(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    populate(campaign)
    first = complete_segment(campaign, 0)
    complete_segment(campaign, 1)
    (first / "segment.complete").unlink()

    with pytest.raises(CleanupSafetyError, match="non-contiguous"):
        build_plan(campaign, [])


def test_manifest_is_required_and_bound_to_static_and_path(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    Path(f"{forcing[0]}.hicarprep-manifest.json").unlink()

    with pytest.raises(CleanupSafetyError, match="publication manifest"):
        build_plan(campaign, [])


def test_ready_marker_must_be_empty(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    Path(f"{forcing[0]}.ready").write_text("not a marker", encoding="utf-8")

    with pytest.raises(CleanupSafetyError, match="ready marker is not empty"):
        build_plan(campaign, [])


def test_apply_revalidates_digest_and_deletes_marker_before_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    plan = build_plan(campaign, [])
    events: list[tuple[str, bool, bool]] = []
    original_unlink = Path.unlink

    def observed_unlink(path: Path, *args, **kwargs):
        events.append((path.name, forcing[0].exists(), Path(f"{forcing[0]}.ready").exists()))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observed_unlink)
    result = apply_plan(campaign, plan, plan["plan_sha256"], [])

    assert result["deleted_count"] == 1
    assert events[0] == (forcing[0].name + ".ready", True, True)
    assert events[1] == (forcing[0].name, True, False)
    assert not forcing[0].exists()
    assert not Path(f"{forcing[0]}.ready").exists()
    assert Path(f"{forcing[0]}.hicarprep-manifest.json").is_file()


def test_apply_repairs_safe_stale_marker_without_missing_payload_failure(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    forcing[0].unlink()
    plan = build_plan(campaign, [])

    result = apply_plan(campaign, plan, plan["plan_sha256"], [])

    assert result["deleted_count"] == 0
    assert result["marker_only_repair_count"] == 1
    assert result["marker_only_repair_paths"] == [str(forcing[0])]
    assert not Path(f"{forcing[0]}.ready").exists()
    assert Path(f"{forcing[0]}.hicarprep-manifest.json").is_file()


def test_apply_rejects_tampered_plan_and_changed_payload(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    complete_segment(campaign, 0)
    plan = build_plan(campaign, [])

    tampered = json.loads(json.dumps(plan))
    tampered["targets"][0]["path"] = str(tmp_path / "outside.nc")
    with pytest.raises(CleanupSafetyError, match="digest mismatch"):
        apply_plan(campaign, tampered, plan["plan_sha256"], [])

    forcing[0].write_bytes(b"changed-size")
    with pytest.raises(CleanupSafetyError, match=r"changed \(bytes, mtime_ns\)"):
        apply_plan(campaign, plan, plan["plan_sha256"], [])
    assert forcing[0].is_file()


def test_apply_rejects_new_live_reference_after_dry_run(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    forcing = populate(campaign)
    attempt = complete_segment(campaign, 0)
    plan = build_plan(campaign, [])
    (attempt.parent / "forcing.txt").write_text(f'"{forcing[0]}"\n', encoding="utf-8")
    (attempt.parent / "attempt-1.job").write_text("345\n", encoding="utf-8")

    with pytest.raises(CleanupSafetyError, match="live cleanup recheck found blockers"):
        apply_plan(
            campaign,
            plan,
            plan["plan_sha256"],
            [ActiveJob("345", "SUSPENDED", "hc-win-000-a1")],
        )
    assert forcing[0].is_file()


def test_apply_rejects_config_change_after_dry_run(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    populate(campaign)
    complete_segment(campaign, 0)
    plan = build_plan(campaign, [])
    payload = json.loads(campaign.config_path.read_text(encoding="utf-8"))
    payload["input_lookahead_segments"] = 2
    campaign.config_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CleanupSafetyError, match="config changed"):
        apply_plan(campaign, plan, plan["plan_sha256"], [])


def test_full_season_input_lists_are_not_cleanup_eligible(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path, full_season_input_lists=True)

    with pytest.raises(CleanupSafetyError, match="segment-local"):
        build_plan(campaign, [])


def test_restart_plan_requires_receipt_and_preserves_latest_checkpoint(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    first = complete_segment(campaign, 0)
    second = complete_segment(campaign, 1)
    first_restart = Path(json.loads((first / "segment.json").read_text())["restart"])
    second_restart = Path(json.loads((second / "segment.json").read_text())["restart"])

    plan = build_plan(campaign, [])

    restarts = [item for item in plan["targets"] if item["kind"] == "restart"]
    assert [item["path"] for item in restarts] == [str(first_restart)]
    assert plan["retained_restarts"] == [
        {
            "bytes": second_restart.stat().st_size,
            "path": str(second_restart),
            "reason": "latest_completed_checkpoint",
            "season": "winter",
        }
    ]
    assert receipt_path(second).is_file()


def test_restart_plan_refuses_missing_or_corrupt_receipt(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    complete_segment(campaign, 0)
    second = complete_segment(campaign, 1)
    receipt = receipt_path(second)
    original = receipt.read_text(encoding="utf-8")
    receipt.unlink()
    with pytest.raises(CleanupSafetyError, match="not durably attested"):
        build_plan(campaign, [])

    receipt.write_text(original.replace("hicar.restart-transition/v1", "unknown"))
    with pytest.raises(CleanupSafetyError, match="not durably attested"):
        build_plan(campaign, [])


def test_restart_apply_removes_payload_and_input_link_but_retains_receipt(
    tmp_path: Path,
) -> None:
    campaign = configured_campaign(tmp_path)
    first = complete_segment(campaign, 0)
    second = complete_segment(campaign, 1)
    first_restart = Path(json.loads((first / "segment.json").read_text())["restart"])
    second_restart = Path(json.loads((second / "segment.json").read_text())["restart"])
    receipt = receipt_path(second)
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    input_link = Path(receipt_payload["restart"]["successor_input_link"]["path"])
    plan = build_plan(campaign, [])

    result = apply_plan(campaign, plan, plan["plan_sha256"], [])

    assert result["deleted_paths"] == [str(first_restart)]
    assert result["restart_links_removed"] == [str(input_link)]
    assert result["restart_receipts_retained"] == [str(receipt)]
    assert not first_restart.exists() and not os.path.lexists(input_link)
    assert second_restart.is_file() and receipt.is_file()
    validate_receipt(
        receipt,
        first,
        second,
        season="winter",
        predecessor_index=0,
        successor_index=1,
        campaign_commit="c" * 40,
    )
    assert not [item for item in build_plan(campaign, [])["targets"] if item["kind"] == "restart"]


def test_live_model_restart_reference_blocks_restart_cleanup(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    first = complete_segment(campaign, 0)
    second = complete_segment(campaign, 1)
    first_restart = Path(json.loads((first / "segment.json").read_text())["restart"])
    job_file = second.parent / "attempt-1.job"
    job_file.write_text("456\n", encoding="utf-8")
    (second.parent / "forcing.txt").write_text("", encoding="utf-8")

    plan = build_plan(campaign, [ActiveJob("456", "COMPLETING", "hc-win-001-a1")])

    assert not [item for item in plan["targets"] if item["kind"] == "restart"]
    assert {"path": str(first_restart), "reason": "referenced_by_live_model"} in plan[
        "blockers"
    ]


def test_missing_latest_restart_always_fails_closed(tmp_path: Path) -> None:
    campaign = configured_campaign(tmp_path)
    latest = complete_segment(campaign, 0)
    restart = Path(json.loads((latest / "segment.json").read_text())["restart"])
    restart.unlink()

    with pytest.raises(CleanupSafetyError, match="latest completed checkpoint"):
        build_plan(campaign, [])
