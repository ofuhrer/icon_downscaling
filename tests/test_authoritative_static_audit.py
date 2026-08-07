from __future__ import annotations

import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

import netCDF4
import numpy as np
from pyproj import CRS


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "case_studies/swiss_200m/fixed_parameters/audit_glamos_glacier_mask.py"


def write_polygon_shapefile(path: Path, points: list[tuple[float, float]]) -> None:
    xmin = min(point[0] for point in points)
    xmax = max(point[0] for point in points)
    ymin = min(point[1] for point in points)
    ymax = max(point[1] for point in points)
    content = (
        struct.pack("<i4d2i", 5, xmin, ymin, xmax, ymax, 1, len(points))
        + struct.pack("<i", 0)
        + b"".join(struct.pack("<2d", *point) for point in points)
    )
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into(">i", header, 24, (100 + 8 + len(content)) // 2)
    struct.pack_into("<2i4d", header, 28, 1000, 5, xmin, ymin, xmax, ymax)
    path.write_bytes(bytes(header) + struct.pack(">2i", 1, len(content) // 2) + content)


def write_static(path: Path, landuse: np.ndarray) -> None:
    with netCDF4.Dataset(path, "w") as dataset:
        dataset.createDimension("x", landuse.shape[1])
        dataset.createDimension("y", landuse.shape[0])
        dataset.createVariable("x", "f8", ("x",))[:] = np.arange(landuse.shape[1])
        dataset.createVariable("y", "f8", ("y",))[:] = np.arange(landuse.shape[0])
        dataset.createVariable("landuse", "i2", ("y", "x"))[:] = landuse
        dataset.createVariable("topo", "f4", ("y", "x"))[:] = 2500.0
        dataset.hicar_projection = CRS.from_epsg(4326).to_wkt()


class AuthoritativeStaticAuditTests(unittest.TestCase):
    def test_glacier_outline_audit_reports_candidate_recall(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shp = root / "glacier.shp"
            write_polygon_shapefile(
                shp,
                [(-0.5, -0.5), (1.5, -0.5), (1.5, 1.5), (-0.5, 1.5), (-0.5, -0.5)],
            )
            archive = root / "glacier.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.write(shp, "SGI_2016_glaciers.shp")
                output.writestr("SGI_2016_glaciers.prj", CRS.from_epsg(4326).to_wkt())
            baseline = root / "baseline.nc"
            candidate = root / "candidate.nc"
            write_static(baseline, np.array([[24, 7, 7], [7, 7, 7], [7, 7, 7]]))
            write_static(candidate, np.array([[24, 24, 7], [24, 24, 7], [7, 7, 7]]))
            report = root / "report.json"
            result = subprocess.run(
                [
                    sys.executable, str(AUDIT), "--source-zip", str(archive),
                    "--source-url", "https://example.invalid/glacier.zip",
                    "--baseline", str(baseline), "--candidate", str(candidate),
                    "--output", str(report),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(report.read_text())
            self.assertEqual(value["geometry"]["cell_center_area_km2"], 0.16)
            self.assertEqual(value["baseline_A"]["snow_ice_recall"], 0.25)
            self.assertEqual(value["candidate_B_C"]["snow_ice_recall"], 1.0)
            self.assertEqual(value["decision"], "GLAMOS_AUDIT_PASS_NO_AUTOMATIC_CORRECTION")
            self.assertTrue(Path(f"{report}.ready").is_file())


if __name__ == "__main__":
    unittest.main()
