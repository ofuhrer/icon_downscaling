#!/usr/bin/env python3
"""Compare NetCDF forcing payloads exactly while allowing provenance metadata to differ."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import netCDF4
import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def attribute_differences(left: object, right: object) -> list[str]:
    left_names = set(left.ncattrs())
    right_names = set(right.ncattrs())
    different = left_names ^ right_names
    for name in left_names & right_names:
        left_value = np.asarray(left.getncattr(name))
        right_value = np.asarray(right.getncattr(name))
        try:
            equal = np.array_equal(left_value, right_value, equal_nan=True)
        except TypeError:
            equal = np.array_equal(left_value, right_value)
        if left_value.shape != right_value.shape or not equal:
            different.add(name)
    return sorted(different)


def variable_payload(left: netCDF4.Variable, right: netCDF4.Variable) -> dict:
    schema_equal = (
        left.dimensions == right.dimensions
        and left.shape == right.shape
        and left.dtype == right.dtype
    )
    result = {
        "dimensions": list(left.dimensions),
        "shape": list(left.shape),
        "dtype": str(left.dtype),
        "schema_equal": schema_equal,
        "attribute_differences": attribute_differences(left, right),
    }
    if not schema_equal:
        return result

    left.set_auto_maskandscale(False)
    right.set_auto_maskandscale(False)
    left_digest = hashlib.sha256()
    right_digest = hashlib.sha256()
    unequal = 0
    maximum_absolute_difference = 0.0
    slices = [()] if left.ndim == 0 else [(index, ...) for index in range(left.shape[0])]
    for selection in slices:
        left_values = np.ascontiguousarray(left[selection])
        right_values = np.ascontiguousarray(right[selection])
        left_digest.update(left_values.tobytes())
        right_digest.update(right_values.tobytes())
        equal = np.equal(left_values, right_values)
        if np.issubdtype(left_values.dtype, np.floating):
            equal |= np.isnan(left_values) & np.isnan(right_values)
            finite = np.isfinite(left_values) & np.isfinite(right_values)
            if np.any(finite):
                maximum_absolute_difference = max(
                    maximum_absolute_difference,
                    float(np.max(np.abs(left_values[finite] - right_values[finite]))),
                )
        unequal += int(np.count_nonzero(~equal))
    result.update(
        {
            "payload_equal": unequal == 0,
            "unequal_values": unequal,
            "maximum_absolute_difference": maximum_absolute_difference,
            "baseline_payload_sha256": left_digest.hexdigest(),
            "candidate_payload_sha256": right_digest.hexdigest(),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in (args.baseline, args.candidate):
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"missing or empty input: {path}")
    if args.output.exists() or Path(f"{args.output}.ready").exists():
        raise SystemExit(f"refusing to overwrite publication: {args.output}")

    with netCDF4.Dataset(args.baseline) as baseline, netCDF4.Dataset(args.candidate) as candidate:
        baseline_names = set(baseline.variables)
        candidate_names = set(candidate.variables)
        common_names = sorted(baseline_names & candidate_names)
        variables = {
            name: variable_payload(baseline.variables[name], candidate.variables[name])
            for name in common_names
        }
        missing = sorted(baseline_names - candidate_names)
        extra = sorted(candidate_names - baseline_names)
        global_attribute_differences = attribute_differences(baseline, candidate)

    payload_equal = not missing and not extra and all(
        result.get("schema_equal") and result.get("payload_equal")
        for result in variables.values()
    )
    assessor = Path(__file__).resolve()
    payload = {
        "schema_version": 1,
        "status": "PASS" if payload_equal else "FAIL",
        "decision": "NUMERICAL_PAYLOAD_IDENTICAL" if payload_equal else "PAYLOAD_DIFFERENCE",
        "assessor": str(assessor),
        "assessor_sha256": sha256(assessor),
        "baseline": str(args.baseline.resolve()),
        "baseline_sha256": sha256(args.baseline),
        "candidate": str(args.candidate.resolve()),
        "candidate_sha256": sha256(args.candidate),
        "file_bytes_identical": sha256(args.baseline) == sha256(args.candidate),
        "global_attribute_differences": global_attribute_differences,
        "missing_variables": missing,
        "extra_variables": extra,
        "variables": variables,
    }
    write_json_atomic(args.output, payload)
    Path(f"{args.output}.ready").write_text(sha256(args.output) + "\n")
    print(f"{payload['status']}: {payload['decision']} {args.output}")
    return 0 if payload_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
