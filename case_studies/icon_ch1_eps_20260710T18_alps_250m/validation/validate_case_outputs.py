#!/usr/bin/env python3
"""Validate the frozen ICON/HICAR 250 m case-study inputs."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


CASE_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_STEPS = 34


def finite_data(var):
    data = np.ma.asarray(var[:])
    if np.ma.isMaskedArray(data):
        data = data.filled(np.nan)
    return np.asarray(data, dtype=np.float64)


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        self.lines.append(f"\n## {title}")

    def info(self, text: str) -> None:
        self.lines.append(text)

    def check(self, condition: bool, text: str) -> None:
        prefix = "PASS" if condition else "FAIL"
        self.lines.append(f"- {prefix}: {text}")
        if not condition:
            self.failures.append(text)

    def stats(self, name: str, values: np.ndarray) -> None:
        finite = np.isfinite(values)
        if not finite.any():
            self.check(False, f"{name} has at least one finite value")
            return
        self.lines.append(
            f"- {name}: min={np.nanmin(values):.6g}, "
            f"max={np.nanmax(values):.6g}, mean={np.nanmean(values):.6g}, "
            f"finite={finite.sum()}/{values.size}"
        )

    def text(self) -> str:
        status = "PASS" if not self.failures else "FAIL"
        header = [f"# Validation Report", f"status: {status}"]
        if self.failures:
            header.append("failures:")
            header.extend(f"- {item}" for item in self.failures)
        return "\n".join(header + self.lines) + "\n"


def validate_static(path: Path, report: Report) -> dict[str, tuple[float, float]]:
    report.section("Static Domain")
    report.check(path.with_name(path.name + ".ready").exists(), f"{path.name}.ready marker is present")
    with Dataset(path) as ds:
        dims = {name: len(dim) for name, dim in ds.dimensions.items()}
        report.info(f"dims: {dims}")
        report.check(dims.get("x") == 81 and dims.get("y") == 81, "static grid is 81 x 81")
        report.check(dims.get("soil_layer") == 4, "static file has 4 soil layers")

        x = finite_data(ds.variables["x"])
        y = finite_data(ds.variables["y"])
        dx = np.diff(x)
        dy = np.diff(y)
        report.check(np.allclose(dx, 250.0), "x spacing is 250 m")
        report.check(np.allclose(dy, 250.0), "y spacing is 250 m")

        lat = finite_data(ds.variables["lat"])
        lon = finite_data(ds.variables["lon"])
        report.stats("lat", lat)
        report.stats("lon", lon)
        report.check(np.all(np.isfinite(lat)) and np.all(np.isfinite(lon)), "lat/lon are finite")

        topo = finite_data(ds.variables["topo"])
        topo_hi = finite_data(ds.variables["topo_highres"])
        topo_drive = finite_data(ds.variables["topo_driving"])
        weight = finite_data(ds.variables["topo_blend_weight"])
        report.stats("topo", topo)
        report.stats("topo_highres", topo_hi)
        report.stats("topo_driving", topo_drive)
        report.stats("topo_blend_weight", weight)
        report.check(np.nanmin(weight) >= -1e-6 and np.nanmax(weight) <= 1.0 + 1e-6, "blend weights are in [0, 1]")
        report.check(np.nanmax(weight[[0, -1], :]) < 1e-6 and np.nanmax(weight[:, [0, -1]]) < 1e-6, "outer boundary uses driving topography")
        report.check(np.nanmax(weight) > 0.99, "domain interior reaches high-resolution topography")
        reconstructed = topo_drive * (1.0 - weight) + topo_hi * weight
        report.check(np.nanmax(np.abs(topo - reconstructed)) < 1e-3, "topography equals documented blend")

        landmask = finite_data(ds.variables["landmask"])
        landuse = finite_data(ds.variables["landuse"])
        soil_type = finite_data(ds.variables["soil_type"])
        soil_vwc = finite_data(ds.variables["soil_vwc"])
        for name, values in [
            ("landmask", landmask),
            ("landuse", landuse),
            ("soil_type", soil_type),
            ("soil_vwc", soil_vwc),
        ]:
            report.stats(name, values)
        report.check(set(np.unique(landmask)).issubset({0.0, 1.0}), "landmask is binary")
        report.check(np.nanmin(landuse) >= 1 and np.nanmax(landuse) <= 24, "landuse classes are in USGS-compatible 1..24 range")
        report.check(np.nanmin(soil_type) >= 1 and np.nanmax(soil_type) <= 16, "soil_type classes are in a Noah-style range")
        report.check(np.nanmin(soil_vwc) >= 0.0 and np.nanmax(soil_vwc) <= 0.8, "soil volumetric water content is plausible")
        return {
            "lat": (float(np.nanmin(lat)), float(np.nanmax(lat))),
            "lon": (float(np.nanmin(lon)), float(np.nanmax(lon))),
        }


FORCING_RANGE_CHECKS = [
    ("P", 1_000.0, 110_000.0),
    ("QV", -1e-8, 0.1),
    ("T", 150.0, 330.0),
    ("U", -200.0, 200.0),
    ("V", -200.0, 200.0),
    ("W", -100.0, 100.0),
    ("HFL", -500.0, 30_000.0),
    ("HHL", -500.0, 30_000.0),
    ("HSURF", -500.0, 6_000.0),
    ("FR_LAND", -1e-6, 1.0 + 1e-6),
]


def validate_forcing_file(path: Path, report: Report, expected_step: int | None = None) -> dict[str, object]:
    with Dataset(path) as ds:
        dims = {name: len(dim) for name, dim in ds.dimensions.items()}
        report.check(dims.get("z") == 80, f"{path.name}: z dimension is 80")
        report.check(dims.get("z_hl") == 81, f"{path.name}: z_hl dimension is 81")
        report.check(dims.get("time") == 1, f"{path.name}: time dimension is singleton")
        report.check(dims.get("y_1") == 125 and dims.get("x_1") == 185, f"{path.name}: forcing grid is 125 x 185")

        time = finite_data(ds.variables["time"])
        if expected_step is not None:
            report.check(time.size == 1 and abs(float(time[0]) - expected_step * 60.0) < 1e-6, f"{path.name}: time is +{expected_step:03d} h")

        ranges = {}
        for var, lo, hi in FORCING_RANGE_CHECKS:
            values = finite_data(ds.variables[var])
            ranges[var] = (float(np.nanmin(values)), float(np.nanmax(values)))
            finite = np.isfinite(values)
            report.check(finite.all(), f"{path.name}: {var} is finite")
            report.check(np.nanmin(values) >= lo and np.nanmax(values) <= hi, f"{path.name}: {var} range is plausible")

        hhl = finite_data(ds.variables["HHL"])
        hfl = finite_data(ds.variables["HFL"])
        report.check(np.nanmin(np.diff(hhl, axis=0)) > -1e-3, f"{path.name}: HHL increases bottom-to-top")
        report.check(np.nanmin(np.diff(hfl, axis=0)) > -1e-3, f"{path.name}: HFL increases bottom-to-top")

        lat = finite_data(ds.variables["lat_1"])
        lon = finite_data(ds.variables["lon_1"])
        return {
            "lat_min": float(np.nanmin(lat)),
            "lat_max": float(np.nanmax(lat)),
            "lon_min": float(np.nanmin(lon)),
            "lon_max": float(np.nanmax(lon)),
            "time": float(time[0]),
            "ranges": ranges,
        }


def validate_forcing(root: Path, static_bbox: dict[str, tuple[float, float]], report: Report) -> None:
    report.section("ICON Forcing")
    files = sorted(root.glob("hicar_forcing_f[0-9][0-9][0-9].nc"))
    report.info(f"forcing_files: {len(files)}")
    report.check(len(files) == EXPECTED_STEPS, f"exactly {EXPECTED_STEPS} hourly forcing files are present")
    ready_files = [path for path in files if path.with_name(path.name + ".ready").exists()]
    report.info(f"forcing_ready_files: {len(ready_files)}")
    report.check(len(ready_files) == len(files), "each forcing file has a .nc.ready marker")
    if not files:
        return

    summaries = []
    for path in files:
        step = int(path.stem.rsplit("_f", 1)[1])
        summaries.append(validate_forcing_file(path, report, step))

    times = np.array([item["time"] for item in summaries])
    report.check(np.array_equal(times, np.arange(len(files)) * 60.0), "forcing times are hourly from +0 h")

    report.section("Forcing Aggregate Ranges")
    for var, _, _ in FORCING_RANGE_CHECKS:
        mins = [item["ranges"][var][0] for item in summaries]
        maxs = [item["ranges"][var][1] for item in summaries]
        report.info(f"- {var}: min={min(mins):.6g}, max={max(maxs):.6g}")

    lat_min = min(item["lat_min"] for item in summaries)
    lat_max = max(item["lat_max"] for item in summaries)
    lon_min = min(item["lon_min"] for item in summaries)
    lon_max = max(item["lon_max"] for item in summaries)
    report.info(f"forcing_lat_range: {lat_min:.6f} .. {lat_max:.6f}")
    report.info(f"forcing_lon_range: {lon_min:.6f} .. {lon_max:.6f}")
    report.check(lat_min <= static_bbox["lat"][0] and lat_max >= static_bbox["lat"][1], "forcing latitude covers static domain")
    report.check(lon_min <= static_bbox["lon"][0] and lon_max >= static_bbox["lon"][1], "forcing longitude covers static domain")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", type=Path, default=CASE_ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    case_root = args.case_root.resolve()
    report = Report()
    report.info(f"case_root: {case_root}")
    static_bbox = validate_static(case_root / "static" / "domain_static_relaxed.nc", report)
    validate_forcing(case_root / "forcing", static_bbox, report)

    text = report.text()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    print(text)
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
