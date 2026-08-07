from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "case_studies/swiss_200m/fixed_parameters/create_static_process_case_plan.py"
RELEASE = (
    ROOT / "case_studies/swiss_200m/fixed_parameters/qualification_artifacts/"
    "static_abc_v1/qualification_release.json"
)


class StaticProcessCasePlanTests(unittest.TestCase):
    def test_plan_is_adaptive_paired_and_initialization_aware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            result = subprocess.run(
                [sys.executable, str(GENERATOR), "--release", str(RELEASE), "--output", str(output)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text())
            self.assertEqual(plan["initialization"]["lead_hours"], [24, 48, 72, 120, 168])
            self.assertEqual(plan["initialization"]["minimum_selected_lead_hours"], 48)
            self.assertIn("do not interpret", plan["initialization"]["selection_rule"])
            self.assertEqual(plan["arms"]["B"]["static_sha256"], plan["arms"]["C"]["static_sha256"])
            self.assertEqual(plan["arms"]["B"]["nmp_opt_soil"], 1)
            self.assertEqual(plan["arms"]["C"]["nmp_opt_soil"], 2)
            self.assertEqual(plan["schema"], "hicar-static-process-case-plan/v3")
            self.assertEqual(len(plan["execution"]["records"]), 60)
            records = plan["execution"]["records"]
            self.assertTrue(all(record["stage"] == "continuous_precondition_and_score" for record in records))
            self.assertTrue(all(record["restart_required"] is False for record in records))
            self.assertTrue(all(record["output_profile"] == "static_process_case" for record in records))
            self.assertIn("not permitted", plan["initialization"]["restart_policy"])
            self.assertTrue(Path(f"{output}.ready").is_file())


if __name__ == "__main__":
    unittest.main()
