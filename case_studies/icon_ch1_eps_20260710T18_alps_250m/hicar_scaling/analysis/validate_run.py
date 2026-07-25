#!/usr/bin/env python3
"""Fail a benchmark run missing its timer block or hourly output."""
import argparse
import subprocess
from pathlib import Path

def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--run", type=Path, required=True); p.add_argument("--expected-hours", type=int, required=True); a = p.parse_args()
    logs = list((a.run / "logs").glob("slurm_*.out")) + list((a.run / "logs").glob("hicar_*.out"))
    if not logs: raise SystemExit("missing HICAR stdout")
    text = max(logs, key=lambda path: path.stat().st_mtime).read_text(errors="replace")
    if "Timing across all compute images:" not in text: raise SystemExit("HICAR timer block missing")
    outputs = list((a.run / "output").glob("*.nc"))
    if not outputs: raise SystemExit("missing NetCDF output")
    # HICAR's default frames_per_outfile=24 packs the six hourly samples (and
    # initial record) into one NetCDF file.  Separate hourly files are also
    # accepted when a different output framing is used.
    if len(outputs) < a.expected_hours and len(outputs) != 1:
        raise SystemExit(f"expected hourly output in one consolidated file or >= {a.expected_hours} files, found {len(outputs)}")
    # A copied/generated static-domain file is valid NetCDF but is not evidence
    # that the integration reached an output time.  HICAR's dynamic output
    # always defines an unlimited `time` dimension and its corresponding
    # variable (output_obj::setup_time_variable).
    dynamic_outputs = []
    for output in outputs:
        header = subprocess.run(
            ["ncdump", "-h", str(output)], text=True, capture_output=True, check=False
        )
        if header.returncode == 0 and "time = UNLIMITED" in header.stdout and "double time(time)" in header.stdout:
            dynamic_outputs.append(output)
    if not dynamic_outputs:
        raise SystemExit("missing dynamic NetCDF output with an unlimited time dimension")
    (a.run / "VALIDATED").write_text("timer/output gate passed\n")

if __name__ == "__main__": main()
