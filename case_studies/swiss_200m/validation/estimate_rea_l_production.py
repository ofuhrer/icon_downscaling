#!/usr/bin/env python3
"""Estimate 20-year REA-L-to-HICAR compute, traffic, and storage."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
DOMAIN = json.loads((CASE / "config" / "domain.json").read_text())
PRODUCER = json.loads(
    (CASE / "validation" / "streaming_forcing_qualification_20200701.json").read_text()
)
COMPRESSION = json.loads(
    (CASE / "validation" / "routine_compression_benchmark.json").read_text()
)
QUALIFICATION = json.loads(
    (CASE / "validation" / "production_6h_qualification_4927314.json").read_text()
)
STREAM_MODEL = json.loads(
    (CASE / "validation" / "streaming_model_qualification_20200701_00_02.json").read_text()
)
STREAM_COMPRESSION_PATH = (
    CASE
    / "validation"
    / "streaming_output_compression_after_z_fix_20200701_02_04.json"
)
if not STREAM_COMPRESSION_PATH.is_file():
    STREAM_COMPRESSION_PATH = (
        CASE / "validation" / "streaming_output_compression_20200701_00_02.json"
    )
STREAM_COMPRESSION = json.loads(STREAM_COMPRESSION_PATH.read_text())

GIB = 1024**3
TIB = 1024**4
START = datetime.fromisoformat("2005-01-01T00:00:00")
END = datetime.fromisoformat("2025-01-01T00:00:00")
SIMULATION_HOURS = int((END - START).total_seconds() / 3600)
UNIQUE_HOURLY_RECORDS = SIMULATION_HOURS + 1
ROUTINE_FIELDS = 11
CHUNK_HOURS = 7 * 24
CHUNKS = math.ceil(SIMULATION_HOURS / CHUNK_HOURS)
OUTPUT_FRAMES_PER_FILE = 24
OUTPUT_FILES = math.ceil(UNIQUE_HOURLY_RECORDS / OUTPUT_FRAMES_PER_FILE)
NODES_200M = 4
GPUS_PER_NODE = 4
BALFRIN_NORMAL_TOTAL_NODES = 46
BALFRIN_NORMAL_MAX_JOB_HOURS = 24
YEAR_CHAINS = 20

# Fit T_model(H) = fixed + rate*H to the 30-minute and six-hour national runs.
SHORT_HOURS = 0.5
SHORT_MODEL_SECONDS = 328.2997
LONG_HOURS = 6.0
LONG_MODEL_SECONDS = 1360.221
RATE_SECONDS_PER_SIM_HOUR = (
    (LONG_MODEL_SECONDS - SHORT_MODEL_SECONDS) / (LONG_HOURS - SHORT_HOURS)
)
MODEL_FIXED_SECONDS = SHORT_MODEL_SECONDS - RATE_SECONDS_PER_SIM_HOUR * SHORT_HOURS
BATCH_MINUS_MODEL_SECONDS = 1411.0 - LONG_MODEL_SECONDS
FIXED_SECONDS_PER_CHUNK = MODEL_FIXED_SECONDS + BATCH_MINUS_MODEL_SECONDS


def compute_case(
    *,
    label: str,
    cells: int,
    nodes: int,
    wall_factor: float,
    uncertainty: tuple[float, float],
) -> dict:
    base_wall_hours = (
        SIMULATION_HOURS * RATE_SECONDS_PER_SIM_HOUR * wall_factor
        + CHUNKS * FIXED_SECONDS_PER_CHUNK * wall_factor
    ) / 3600
    chunk_wall_hours = (
        CHUNK_HOURS * RATE_SECONDS_PER_SIM_HOUR * wall_factor
        + FIXED_SECONDS_PER_CHUNK * wall_factor
    ) / 3600
    uncertainty_scale = (
        uncertainty[0] / wall_factor,
        uncertainty[1] / wall_factor,
    )
    annual_chain_wall_hours = base_wall_hours / YEAR_CHAINS
    maximum_concurrent_chains = BALFRIN_NORMAL_TOTAL_NODES // nodes
    if maximum_concurrent_chains < 1:
        raise ValueError(
            f"{nodes} nodes per chain exceeds Balfrin normal capacity"
        )
    capacity_waves = math.ceil(YEAR_CHAINS / maximum_concurrent_chains)
    dynamic_output_bytes = cells * ROUTINE_FIELDS * 4 * UNIQUE_HOURLY_RECORDS
    coordinate_output_bytes = cells * 2 * 4 * OUTPUT_FILES
    output_raw_tib = (dynamic_output_bytes + coordinate_output_bytes) / TIB
    measured_ratio = float(STREAM_COMPRESSION["physical_size_ratio"])
    restart_bytes = (
        STREAM_MODEL["restart"]["size_bytes"]
        * cells
        / int(DOMAIN["horizontal_cells"])
    )
    return {
        "label": label,
        "horizontal_cells": cells,
        "nodes": nodes,
        "gpus": nodes * GPUS_PER_NODE,
        "wall_factor_relative_to_measured_200m": wall_factor,
        "seven_day_chunk_wall_hours_base": chunk_wall_hours,
        "seven_day_chunk_wall_hours_range": [
            chunk_wall_hours * uncertainty_scale[0],
            chunk_wall_hours * uncertainty_scale[1],
        ],
        "seven_day_chunk_fits_normal_24h_at_range_high": (
            chunk_wall_hours * uncertainty_scale[1]
            < BALFRIN_NORMAL_MAX_JOB_HOURS
        ),
        "twenty_year_aggregate_wall_hours_base": base_wall_hours,
        "twenty_year_aggregate_wall_hours_range": [
            base_wall_hours * uncertainty[0] / wall_factor,
            base_wall_hours * uncertainty[1] / wall_factor,
        ],
        "twenty_year_node_hours_base": base_wall_hours * nodes,
        "twenty_year_gpu_hours_base": base_wall_hours * nodes * GPUS_PER_NODE,
        "one_chain_calendar_days_base": base_wall_hours / 24,
        "unconstrained_twenty_parallel_year_chains_calendar_days_base": (
            annual_chain_wall_hours / 24
        ),
        "unconstrained_twenty_parallel_year_chains_nodes": nodes * YEAR_CHAINS,
        "unconstrained_twenty_parallel_year_chains_gpus": (
            nodes * GPUS_PER_NODE * YEAR_CHAINS
        ),
        "balfrin_normal_capacity": {
            "total_nodes": BALFRIN_NORMAL_TOTAL_NODES,
            "maximum_job_hours": BALFRIN_NORMAL_MAX_JOB_HOURS,
            "maximum_concurrent_year_chains": maximum_concurrent_chains,
            "nodes_at_maximum_concurrency": maximum_concurrent_chains * nodes,
            "year_chain_waves_for_twenty_years": capacity_waves,
            "exclusive_capacity_calendar_days_base": (
                capacity_waves * annual_chain_wall_hours / 24
            ),
            "exclusive_capacity_calendar_days_range": [
                capacity_waves
                * annual_chain_wall_hours
                * uncertainty_scale[0]
                / 24,
                capacity_waves
                * annual_chain_wall_hours
                * uncertainty_scale[1]
                / 24,
            ],
            "interpretation": (
                "Theoretical lower bound if the listed share of normal is "
                "continuously available; queue delay, production/failover "
                "demand, and failed retries make elapsed time longer."
            ),
        },
        "routine_hourly_output": {
            "fields_2d_float32": ROUTINE_FIELDS,
            "files_with_static_lat_lon": OUTPUT_FILES,
            "raw_dynamic_fields_tib": dynamic_output_bytes / TIB,
            "raw_repeated_lat_lon_tib": coordinate_output_bytes / TIB,
            "raw_tib": output_raw_tib,
            "compressed_tib_at_measured_ratio": output_raw_tib * measured_ratio,
            "compressed_tib_range_ratio_0.5_to_0.8": [
                output_raw_tib * 0.5,
                output_raw_tib * 0.8,
            ],
        },
        "restart_storage": {
            "estimated_bytes_per_boundary": restart_bytes,
            "rolling_two_boundaries_gib": 2 * restart_bytes / GIB,
            "twenty_annual_checkpoints_tib": 20 * restart_bytes / TIB,
            "all_seven_day_boundaries_tib_not_recommended": CHUNKS
            * restart_bytes
            / TIB,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=CASE / "validation" / "rea_l_20year_resource_estimate.json",
    )
    args = parser.parse_args()

    cells_200m = int(DOMAIN["horizontal_cells"])
    cells_100m = (2 * int(DOMAIN["nx"]) - 1) * (2 * int(DOMAIN["ny"]) - 1)
    cell_ratio_100m = cells_100m / cells_200m
    mean_forcing = float(PRODUCER["mean_forcing_bytes"])
    dynamic_source = (
        float(PRODUCER["transient_source_bytes_read"]) / PRODUCER["records"]
    )
    payload = {
        "status": "ESTIMATE",
        "period": {
            "start": START.isoformat(),
            "end_exclusive": END.isoformat(),
            "days": (END - START).days,
            "simulation_hours": SIMULATION_HOURS,
            "unique_hourly_records": UNIQUE_HOURLY_RECORDS,
        },
        "production_unit": {
            "chunk_hours": CHUNK_HOURS,
            "chunks": CHUNKS,
            "planned_records_in_separate_chunk_directories": SIMULATION_HOURS + CHUNKS,
            "output_profile": [
                "precipitation",
                "psfc",
                "taix",
                "hus2m",
                "u10m",
                "v10m",
                "rsds",
                "lwtr",
                "rlus",
                "hfgs",
                "emiss",
            ],
        },
        "balfrin_partition_snapshot": {
            "verified": "2026-07-25",
            "partition": "normal",
            "total_nodes": BALFRIN_NORMAL_TOTAL_NODES,
            "gpus_per_node": GPUS_PER_NODE,
            "maximum_job_hours": BALFRIN_NORMAL_MAX_JOB_HOURS,
        },
        "measurements": {
            "producer_mean_forcing_bytes_per_hour": mean_forcing,
            "producer_dynamic_fdb_bytes_per_hour": dynamic_source,
            "producer_worker_seconds_range": [61, 85],
            "compression_ratio_two_field_sample_including_coordinates": COMPRESSION[
                "compressed_to_logical_ratio"
            ],
            "national_200m_model_seconds_30min": SHORT_MODEL_SECONDS,
            "national_200m_model_seconds_6h": LONG_MODEL_SECONDS,
            "fitted_seconds_per_simulation_hour_200m": RATE_SECONDS_PER_SIM_HOUR,
            "fitted_fixed_seconds_per_chunk_200m": FIXED_SECONDS_PER_CHUNK,
            "qualified_peak_task_rss_gib": QUALIFICATION["model"][
                "peak_step_rss_gib"
            ],
            "streaming_200m_model_seconds_2h": 661.6086,
            "streaming_200m_output_bytes_3_records_before_z_fix": STREAM_MODEL[
                "output"
            ]["size_bytes"],
            "streaming_200m_restart_bytes": STREAM_MODEL["restart"]["size_bytes"],
            "streaming_output_compression_seconds_before_z_fix": 42,
            "streaming_output_compression_seconds_after_z_fix": 12,
            "streaming_output_compression_report": str(STREAM_COMPRESSION_PATH),
            "streaming_output_compression_ratio": STREAM_COMPRESSION[
                "physical_size_ratio"
            ],
        },
        "transient_input": {
            "200m": {
                "forcing_if_all_retained_tib": mean_forcing
                * UNIQUE_HOURLY_RECORDS
                / TIB,
                "seven_day_forcing_ring_gib": mean_forcing
                * (CHUNK_HOURS + 1)
                / GIB,
            },
            "100m_capacity_scaled": {
                "forcing_if_all_retained_tib": mean_forcing
                * cell_ratio_100m
                * UNIQUE_HOURLY_RECORDS
                / TIB,
                "seven_day_forcing_ring_gib": mean_forcing
                * cell_ratio_100m
                * (CHUNK_HOURS + 1)
                / GIB,
            },
            "twenty_year_dynamic_fdb_read_tib": dynamic_source
            * UNIQUE_HOURLY_RECORDS
            / TIB,
            "twenty_year_daily_static_fdb_read_tib": 99_983_603
            * (END - START).days
            / TIB,
        },
        "compute": {
            "200m": compute_case(
                label="measured 200 m national domain",
                cells=cells_200m,
                nodes=NODES_200M,
                wall_factor=1.0,
                uncertainty=(0.8, 1.5),
            ),
            "100m": compute_case(
                label="capacity-scaled 100 m national domain",
                cells=cells_100m,
                nodes=16,
                wall_factor=2.0,
                uncertainty=(1.5, 3.0),
            ),
        },
        "assumptions_and_uncertainty": [
            "The 200 m time model is fitted from the qualified 30-minute and six-hour runs and agrees with the live two-hour streaming run; seven-day restart-write overhead is extrapolated from one boundary.",
            "The 100 m base case holds cells per GPU approximately constant with 64 GPUs and assumes a half-sized stable time step, hence twice the wall time.",
            "The 100 m wall range is 1.5--3.0 times the measured 200 m wall time until a live capacity/restart benchmark exists.",
            "The former all-years-parallel calendar figures required 80 nodes at 200 m and 320 nodes at 100 m and are impossible on Balfrin normal's current 46 nodes. Capacity-wave estimates assume exclusive continuous access and are still optimistic.",
            "Output storage uses eleven hourly two-dimensional float32 fields plus repeated two-dimensional lat/lon coordinates. Full three-dimensional hourly output is excluded.",
            "The first streaming output unintentionally contained a 1.2 GB static 3-D z field because restart-only 3-D state triggered history-coordinate output. HICAR commit cef7e3d6 removes that field from 2-D-only history; the output estimate assumes the fix.",
            "Restart storage must be rolling: retaining every seven-day restart would consume roughly 40 TiB at 200 m and four times that at 100 m.",
            "The observed compression sample is small; 0.5--0.8 of raw bytes is the planning range.",
            "Queue delay, failed retries, spin-up, yearly state initialization, and archive-copy costs are excluded.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
