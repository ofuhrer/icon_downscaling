import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "archive_recovery_plan.py"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_plan(
    tmp_path: Path,
    source: Path,
    *,
    expected_sha256: str | None,
    destination: str = "artifacts/source.bin",
) -> Path:
    archive = tmp_path / "store" / "icon_downscaling"
    plan = {
        "archive_id": "test-archive",
        "archive_root": str(archive),
        "files": [
            {
                "classification": "test",
                "destination": destination,
                "expected_sha256": expected_sha256,
                "id": "source",
                "source": str(source),
            }
        ],
        "manifest": str(archive / "manifests" / "archive.json"),
        "schema_version": 1,
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def run_plan(plan: Path, allowed: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--plan",
            str(plan),
            "--allowed-destination-prefix",
            str(allowed),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_archive_publication_is_hash_verified_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"recoverable payload")
    plan = write_plan(tmp_path, source, expected_sha256=file_sha256(source))
    allowed = tmp_path / "store" / "icon_downscaling"

    first = run_plan(plan, allowed)
    assert first.returncode == 0, first.stderr
    archived = allowed / "artifacts" / "source.bin"
    report = archived.with_name("source.bin.archive.json")
    manifest = allowed / "manifests" / "archive.json"
    assert archived.read_bytes() == source.read_bytes()
    assert report.with_name("source.bin.archive.json.ready").exists()
    assert archived.with_name("source.bin.ready").exists()
    assert manifest.with_name("archive.json.sha256").exists()
    assert manifest.with_name("archive.json.ready").exists()
    assert json.loads(report.read_text())["sha256"] == file_sha256(source)

    second = run_plan(plan, allowed)
    assert second.returncode == 0, second.stderr
    assert json.loads(manifest.read_text())["status"] == "PASS"


def test_archive_rejects_wrong_source_hash_without_publication(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"unexpected")
    plan = write_plan(tmp_path, source, expected_sha256="0" * 64)
    allowed = tmp_path / "store" / "icon_downscaling"

    result = run_plan(plan, allowed)

    assert result.returncode == 2
    assert "does not match" in result.stderr
    assert not (allowed / "artifacts" / "source.bin").exists()


def test_archive_rejects_destination_traversal(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    plan = write_plan(
        tmp_path,
        source,
        expected_sha256=file_sha256(source),
        destination="../escaped.bin",
    )
    allowed = tmp_path / "store" / "icon_downscaling"

    result = run_plan(plan, allowed)

    assert result.returncode == 2
    assert "unsafe archive destination" in result.stderr
    assert not (tmp_path / "store" / "escaped.bin").exists()


def test_archive_refuses_incomplete_existing_publication(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    plan = write_plan(tmp_path, source, expected_sha256=file_sha256(source))
    allowed = tmp_path / "store" / "icon_downscaling"
    destination = allowed / "artifacts" / "source.bin"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"partial publication")

    result = run_plan(plan, allowed)

    assert result.returncode == 2
    assert "incomplete existing publication" in result.stderr


def test_archive_rejects_payload_that_collides_with_a_ready_marker(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    marker = tmp_path / "source.bin.ready"
    source.write_bytes(b"payload")
    marker.write_bytes(b"")
    archive = tmp_path / "store" / "icon_downscaling"
    plan = {
        "archive_id": "test-archive",
        "archive_root": str(archive),
        "files": [
            {
                "classification": "test",
                "destination": "artifacts/source.bin",
                "expected_sha256": file_sha256(source),
                "id": "source",
                "source": str(source),
            },
            {
                "classification": "test",
                "destination": "artifacts/source.bin.ready",
                "expected_sha256": file_sha256(marker),
                "id": "source-marker",
                "source": str(marker),
            },
        ],
        "manifest": str(archive / "manifests" / "archive.json"),
        "schema_version": 1,
    }
    plan_path = tmp_path / "collision-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = run_plan(plan_path, archive)

    assert result.returncode == 2
    assert "archive publication path collision" in result.stderr
