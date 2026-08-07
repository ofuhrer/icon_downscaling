#!/usr/bin/env python3
"""Render the frozen V29 namelist, then make its one cloud-pathway change."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


BASE_RENDERER = Path(
    "/scratch/mch/olifu/icon_hicar/causal_resolution/v1/runtime/"
    "case_studies/swiss_200m/scripts/render_hicar_namelist.py"
)


def main() -> int:
    if not BASE_RENDERER.is_file():
        raise SystemExit(f"missing frozen base renderer: {BASE_RENDERER}")
    subprocess.run([sys.executable, str(BASE_RENDERER), *sys.argv[1:]], check=True)
    try:
        target = Path(sys.argv[sys.argv.index("--output") + 1])
    except (ValueError, IndexError) as exc:
        raise SystemExit("the frozen renderer requires --output") from exc
    contents = target.read_text()
    source = "  mp = 'morrison'"
    replacement = "  mp = 'thompson'"
    if contents.count(source) != 1:
        raise SystemExit("frozen V29 namelist lacks a unique Morrison setting")
    target.write_text(contents.replace(source, replacement))
    if target.read_text().count(replacement) != 1:
        raise SystemExit("failed to apply the Thompson-only correction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
