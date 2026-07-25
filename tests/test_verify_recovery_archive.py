import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVER = ROOT / "scripts" / "archive_recovery_plan.py"
VERIFIER = ROOT / "scripts" / "verify_recovery_archive.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source.bin"
    source.write_bytes(b"independent archive readback")
    archive = tmp_path / "store" / "icon_downscaling"
    manifest = archive / "manifests" / "archive.json"
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "archive_id": "verify-test",
                "archive_root": str(archive),
                "files": [
                    {
                        "classification": "test",
                        "destination": "artifacts/source.bin",
                        "expected_sha256": sha256(source),
                        "id": "source",
                        "source": str(source),
                    }
                ],
                "manifest": str(manifest),
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(ARCHIVER),
            "--plan",
            str(plan),
            "--allowed-destination-prefix",
            str(archive),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return archive, manifest


def verify(archive: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--manifest",
            str(manifest),
            "--allowed-destination-prefix",
            str(archive),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_verifier_reads_back_complete_publication(tmp_path: Path) -> None:
    archive, manifest = publish(tmp_path)

    result = verify(archive, manifest)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "PASS"


def test_verifier_detects_payload_corruption(tmp_path: Path) -> None:
    archive, manifest = publish(tmp_path)
    payload = archive / "artifacts" / "source.bin"
    payload.chmod(0o640)
    payload.write_bytes(b"corrupt")

    result = verify(archive, manifest)

    assert result.returncode == 2
    assert "size mismatch" in result.stderr or "SHA-256 mismatch" in result.stderr
