#!/usr/bin/env python3
"""Reject a HICAR log whose physical variational-wind solve did not converge."""

import argparse
import re
from pathlib import Path


STATUS = re.compile(r"HICAR BiCGStab status=\s*(\d+)\s+iterations=\s*(\d+)")
INITIAL = re.compile(r"Residual at iter 0:\s*([-+0-9.Ee]+)")
FINAL = re.compile(r"Residual at final iter:\s*([-+0-9.Ee]+)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--relative-tolerance", type=float, default=1.0e-5)
    args = parser.parse_args()

    if args.relative_tolerance <= 0:
        raise SystemExit("--relative-tolerance must be positive")
    text = args.log.read_text(errors="replace")
    statuses = list(STATUS.finditer(text))
    initials = [float(match.group(1)) for match in INITIAL.finditer(text)]
    finals = [float(match.group(1)) for match in FINAL.finditer(text)]
    if not statuses or len(statuses) != len(initials) or len(statuses) != len(finals):
        raise SystemExit("incomplete HICAR BiCGStab telemetry")

    physical = [
        (int(status.group(1)), int(status.group(2)), initial, final)
        for status, initial, final in zip(statuses, initials, finals)
        if initial > 0.0
    ]
    if not physical:
        raise SystemExit("no physical (non-zero RHS) wind solve found")
    status, iterations, initial, final = physical[-1]
    ratio = final / initial
    print(
        "physical_wind_solve "
        f"status={status} iterations={iterations} initial_residual={initial:.12g} "
        f"final_residual={final:.12g} relative_residual={ratio:.12g}"
    )
    if status != 0:
        raise SystemExit("wind solver hit an iteration cap or breakdown")
    if ratio > args.relative_tolerance:
        raise SystemExit("wind solver residual exceeds acceptance tolerance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
