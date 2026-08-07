#!/usr/bin/env python3
"""Publish the reviewed authoritative Swiss static-reference inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def remote_size(url: str) -> int:
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            return int(content_range.rsplit("/", 1)[1])
        length = response.headers.get("Content-Length")
        if not length:
            raise ValueError(f"server did not report size: {url}")
        return int(length)


def publish(api_snapshot: Path, glacier_zip: Path, glacier_audit: Path, output: Path) -> dict:
    if output.exists() or Path(f"{output}.ready").exists():
        raise ValueError(f"refusing to overwrite inventory: {output}")
    api = json.loads(api_snapshot.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in api["items"]}
    chosen = by_id["swisstlm3d_2021-04"]
    latest = max(api["items"], key=lambda item: item["datetime"])
    shp = next(asset for asset in chosen["assets"] if asset["id"].endswith(".shp.zip"))
    inventory = {
        "schema": "hicar-authoritative-static-reference-inventory/v1",
        "status": "GLAMOS_AUDITED_TLM_AND_TERRAIN_RETRIEVAL_BOUNDED",
        "swissTLM3D": {
            "official_api": "https://ogd.swisstopo.admin.ch/services/swiseld/services/collections/ch.swisstopo.swisstlm3d/assets",
            "api_snapshot": {"path": str(api_snapshot), "sha256": digest(api_snapshot)},
            "audit_release": chosen["id"],
            "audit_asset": {**shp, "size_bytes": remote_size(shp["href"])},
            "latest_observed_release": latest["id"],
            "selection_reason": "2021 release aligns with ESA WorldCover 2021 candidate epoch",
            "retrieval_decision": (
                "Do not download the 3.14 GB national archive for an initial audit. "
                "First implement HTTP-range/layer extraction or use the two bounded process tiles."
            ),
        },
        "GLAMOS_SGI2016": {
            "official_url": "https://doi.glamos.ch/data/inventory/inventory_sgi2016_r2020.zip",
            "cached_zip": {"path": str(glacier_zip), "sha256": digest(glacier_zip), "size_bytes": glacier_zip.stat().st_size},
            "audit": {"path": str(glacier_audit), "sha256": digest(glacier_audit)},
        },
        "swissALTI3D": {
            "official_page": "https://www.swisstopo.admin.ch/en/height-model-swissalti3d",
            "reviewed_variant": "2 m COG, LV95/LN02, 1 km tiles",
            "reported_full_coverage_size": "44 GB",
            "retrieval_decision": "Retrieve only checksum-bound tiles intersecting the two 20.2 km qualification domains.",
        },
        "next_actions": [
            "review GLAMOS disagreement by debris cover and epoch before any ice overwrite",
            "extract swissTLM3D ground-cover and hydrography layers for the two qualification tiles",
            "retrieve 2 m swissALTI3D tiles for area-mean terrain/seam sensitivity on those tiles",
        ],
        "promotion_limit": "Inventory and glacier audit do not promote any national static factor.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(inventory, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        Path(f"{output}.ready").write_text(f"sha256 {digest(output)}  {output.name}\n", encoding="utf-8")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-snapshot", required=True, type=Path)
    parser.add_argument("--glacier-zip", required=True, type=Path)
    parser.add_argument("--glacier-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        value = publish(
            args.api_snapshot.resolve(), args.glacier_zip.resolve(),
            args.glacier_audit.resolve(), args.output.resolve(),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": value["status"], "release": value["swissTLM3D"]["audit_release"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
