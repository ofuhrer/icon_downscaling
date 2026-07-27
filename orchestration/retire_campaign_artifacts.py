#!/usr/bin/env python3
"""Journal and retire verified pre-emptible campaign artifacts safely."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def payload_sha256(payload: dict[str, Any]) -> str:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(content.encode()).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def publish(path: Path, payload: dict[str, Any]) -> None:
    marker = Path(f"{path}.ready")
    marker.unlink(missing_ok=True)
    write_json_atomic(path, payload)
    marker.touch()


def load_published(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or not Path(f"{path}.ready").is_file():
        raise ValueError(f"{label} is not published: {path}")
    payload = json.loads(path.read_text())
    if payload.get("status") != "PASS":
        raise ValueError(f"{label} is not PASS: {path}")
    return payload


def confined(root: Path, path: Path, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"{label} is outside the campaign root: {resolved}")
    return resolved


def matching_existing_report(
    report: Path,
    task: dict[str, Any],
) -> dict[str, Any] | None:
    if not report.is_file():
        return None
    payload = json.loads(report.read_text())
    if (
        payload.get("task_id") != task["task_id"]
        or payload.get("task_sha256") != payload_sha256(task)
    ):
        raise ValueError(f"retirement report belongs to a different task: {report}")
    if Path(f"{report}.ready").is_file():
        if payload.get("status") != "PASS" or payload.get("action") not in {
            "PRESERVED",
            "RETIRED",
        }:
            raise ValueError(f"published retirement report is invalid: {report}")
    elif payload.get("status") != "RETIRING":
        raise ValueError(f"unpublished retirement journal is invalid: {report}")
    return payload


def verified_compressions(
    completion: dict[str, Any],
    task: dict[str, Any],
    root: Path,
) -> list[dict[str, Any]]:
    expected = {
        str(Path(item["path"]).resolve()): item
        for item in completion.get("output", {}).get("files", [])
    }
    if not expected:
        raise ValueError("model completion has no output artifacts")
    results = []
    seen: set[str] = set()
    for item in task["compressions"]:
        source = confined(root, Path(item["source"]), "raw model output")
        target = confined(root, Path(item["target"]), "compressed model output")
        report_path = confined(
            root, Path(item["report"]), "compression report"
        )
        source_key = str(source)
        if source_key in seen or source_key not in expected:
            raise ValueError(f"compression task does not match model output: {source}")
        seen.add(source_key)
        source_entry = expected[source_key]
        compression = load_published(report_path, "compression report")
        if not target.is_file() or not Path(f"{target}.ready").is_file():
            raise ValueError(f"compressed output is not published: {target}")
        if (
            compression.get("source") != source_key
            or compression.get("source_sha256") != source_entry["sha256"]
            or compression.get("target") != str(target)
            or compression.get("target_sha256") != sha256(target)
        ):
            raise ValueError(f"compression evidence does not match: {report_path}")
        results.append(
            {
                "source": source_key,
                "source_sha256": source_entry["sha256"],
                "target": str(target),
                "target_sha256": compression["target_sha256"],
                "report": str(report_path),
                "report_sha256": sha256(report_path),
            }
        )
    if seen != set(expected):
        raise ValueError("compression tasks do not cover every model output")
    return results


def segment_journal(task: dict[str, Any]) -> dict[str, Any]:
    root = Path(task["campaign_root"]).resolve()
    completion_path = confined(
        root, Path(task["model_completion"]), "model completion"
    )
    forcing_path = confined(
        root, Path(task["forcing_publication"]), "forcing publication"
    )
    plan_path = confined(root, Path(task["plan"]), "chunk plan")
    completion = load_published(completion_path, "model completion")
    forcing = load_published(forcing_path, "forcing publication")
    plan = json.loads(plan_path.read_text())
    if (
        completion.get("plan_sha256") not in {None, sha256(plan_path)}
        or forcing.get("plan_sha256") not in {None, sha256(plan_path)}
    ):
        raise ValueError("segment publications do not identify the chunk plan")

    compressions = verified_compressions(completion, task, root)
    targets: list[dict[str, Any]] = [
        {
            "kind": "raw_output",
            "path": item["source"],
            "sha256": item["source_sha256"],
            "ready_marker": None,
        }
        for item in compressions
    ]

    forcing_entries = {
        str(Path(item["forcing_file"]).resolve()): item
        for item in forcing.get("entries", [])
    }
    for record in plan.get("records", []):
        path = confined(root, Path(record["forcing_file"]), "forcing payload")
        entry = forcing_entries.get(str(path))
        if entry is None:
            raise ValueError(f"forcing publication lacks planned payload: {path}")
        if not path.is_file() or sha256(path) != entry["forcing_sha256"]:
            raise ValueError(f"forcing payload changed before retirement: {path}")
        marker = Path(f"{path}.ready")
        if not marker.is_file():
            raise ValueError(f"forcing payload is not published: {path}")
        targets.append(
            {
                "kind": "forcing",
                "path": str(path),
                "sha256": entry["forcing_sha256"],
                "ready_marker": str(marker),
            }
        )

    forcing_marker = Path(f"{forcing_path}.ready")
    cache_root = confined(root, Path(plan["chunk_root"]) / "cache", "forcing cache")
    cache_files = []
    if cache_root.exists():
        for path in sorted(cache_root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"forcing cache contains a symlink: {path}")
            if path.is_file():
                resolved = confined(root, path, "forcing cache file")
                cache_files.append(
                    {
                        "kind": "forcing_cache",
                        "path": str(resolved),
                        "sha256": sha256(resolved),
                        "ready_marker": None,
                    }
                )
    targets.extend(cache_files)

    obsolete_dirs = []
    for value in task.get("obsolete_attempt_dirs", []):
        path = confined(root, Path(value), "obsolete attempt directory")
        if path.is_symlink():
            raise ValueError(f"obsolete attempt directory is a symlink: {path}")
        obsolete_dirs.append(str(path))

    return {
        "schema_version": 1,
        "status": "RETIRING",
        "action": "RETIRING",
        "task_id": task["task_id"],
        "task_sha256": payload_sha256(task),
        "campaign_root": str(root),
        "model_completion": str(completion_path),
        "model_completion_sha256": sha256(completion_path),
        "forcing_publication": str(forcing_path),
        "forcing_publication_sha256": sha256(forcing_path),
        "forcing_publication_marker": str(forcing_marker),
        "plan": str(plan_path),
        "plan_sha256": sha256(plan_path),
        "compressions": compressions,
        "targets": targets,
        "obsolete_attempt_dirs": obsolete_dirs,
    }


def restart_journal(task: dict[str, Any]) -> dict[str, Any]:
    root = Path(task["campaign_root"]).resolve()
    previous_path = confined(
        root, Path(task["previous_completion"]), "previous model completion"
    )
    previous = load_published(previous_path, "previous model completion")
    restart = confined(
        root, Path(previous["restart"]["path"]), "previous restart"
    )
    if not restart.is_file() or sha256(restart) != previous["restart"]["sha256"]:
        raise ValueError(f"previous restart changed before retirement: {restart}")

    next_path = None
    next_sha = None
    if task.get("next_completion"):
        next_path = confined(
            root, Path(task["next_completion"]), "next model completion"
        )
        successor = load_published(next_path, "next model completion")
        if previous.get("end") != successor.get("start"):
            raise ValueError("restart completions are not adjacent")
        if successor.get("restart", {}).get("path") == str(restart):
            raise ValueError("adjacent completions reference the same restart")
        next_sha = sha256(next_path)
    elif not task.get("preserve"):
        raise ValueError("restart retirement requires a successor completion")

    return {
        "schema_version": 1,
        "status": "RETIRING",
        "action": "RETIRING",
        "task_id": task["task_id"],
        "task_sha256": payload_sha256(task),
        "campaign_root": str(root),
        "previous_completion": str(previous_path),
        "previous_completion_sha256": sha256(previous_path),
        "next_completion": str(next_path) if next_path else None,
        "next_completion_sha256": next_sha,
        "preserve": bool(task.get("preserve")),
        "targets": [
            {
                "kind": "restart",
                "path": str(restart),
                "sha256": previous["restart"]["sha256"],
                "ready_marker": None,
            }
        ],
        "obsolete_attempt_dirs": [],
    }


def remove_journaled_targets(journal: dict[str, Any]) -> tuple[int, int]:
    root = Path(journal["campaign_root"]).resolve()
    deleted_bytes = 0
    deleted_files = 0
    for item in journal["targets"]:
        path = confined(root, Path(item["path"]), item["kind"])
        marker_value = item.get("ready_marker")
        if marker_value:
            marker = confined(root, Path(marker_value), f"{item['kind']} marker")
            marker.unlink(missing_ok=True)
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"retirement target is not a regular file: {path}")
        if sha256(path) != item["sha256"]:
            raise ValueError(f"retirement target changed after journaling: {path}")
        deleted_bytes += path.stat().st_size
        path.unlink()
        deleted_files += 1

    for value in journal.get("obsolete_attempt_dirs", []):
        directory = confined(root, Path(value), "obsolete attempt directory")
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"obsolete attempt target is unsafe: {directory}")
        shutil.rmtree(directory)
    return deleted_files, deleted_bytes


def execute_task(task: dict[str, Any]) -> dict[str, Any]:
    report = Path(task["report"]).resolve()
    existing = matching_existing_report(report, task)
    if existing and Path(f"{report}.ready").is_file():
        return existing

    if existing is None:
        if task["kind"] == "segment_retirement":
            journal = segment_journal(task)
        elif task["kind"] == "restart_retirement":
            journal = restart_journal(task)
        else:
            raise ValueError(f"unsupported retirement task: {task['kind']}")
        write_json_atomic(report, journal)
    else:
        journal = existing

    if task["kind"] == "restart_retirement" and journal.get("preserve"):
        target = Path(journal["targets"][0]["path"])
        if not target.is_file() or sha256(target) != journal["targets"][0]["sha256"]:
            raise ValueError(f"preserved restart changed: {target}")
        final = {
            **journal,
            "status": "PASS",
            "action": "PRESERVED",
            "deleted_file_count": 0,
            "deleted_bytes": 0,
        }
    else:
        deleted_files, deleted_bytes = remove_journaled_targets(journal)
        forcing_marker = journal.get("forcing_publication_marker")
        if forcing_marker:
            confined(
                Path(journal["campaign_root"]),
                Path(forcing_marker),
                "forcing publication marker",
            ).unlink(missing_ok=True)
        final = {
            **journal,
            "status": "PASS",
            "action": "RETIRED",
            "deleted_file_count": deleted_files,
            "deleted_bytes": deleted_bytes,
        }
    publish(report, final)
    return final


def selected_task(
    task_file: Path,
    expected_sha256: str,
    index: int,
) -> dict[str, Any]:
    content = task_file.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("campaign CPU task file checksum changed")
    payload = json.loads(content)
    tasks = payload.get("tasks")
    if payload.get("schema_version") != 1 or not isinstance(tasks, list):
        raise ValueError("invalid campaign CPU task publication")
    if index < 0 or index >= len(tasks):
        raise ValueError(f"task index {index} is outside 0..{len(tasks) - 1}")
    return tasks[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-file", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--index", required=True, type=int)
    args = parser.parse_args()
    task = selected_task(args.task_file, args.expected_sha256, args.index)
    result = execute_task(task)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
