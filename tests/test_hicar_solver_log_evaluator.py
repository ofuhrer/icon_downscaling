import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "validation"
    / "evaluate_hicar_solver_log.py"
)
SPEC = importlib.util.spec_from_file_location("solver_log_evaluator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def valid_log(hours=2, rap_levels=9, declared_rap_levels=None):
    if declared_rap_levels is None:
        declared_rap_levels = rap_levels
    lines = [
        " HICAR SLEVE geometry gate: minimum_mass_jacobian= 1.7E-01 "
        "minimum_interface_thickness= 1.2E+01",
        " HICAR multilevel R A P verification: host_stencil= 3.4E-16 "
        "device_halo= 0.0E+00 device_stencil= 3.4E-16",
    ]
    for level in range(2, rap_levels + 1):
        lines.append(
            f" HICAR recursive R A P verification level {level}: "
            "host_stencil= 4.0E-16 device_halo= 0.0E+00 "
            "device_stencil= 4.0E-16"
        )
    lines.append(
        " HICAR exact Galerkin hierarchy ready: total coarse levels="
        f"{declared_rap_levels}"
    )
    lines.extend(
        [
            " HICAR terminal collective solve gate: global=6x5x82 "
            "interior_unknowns=960 iterations=27 relative_residual= 8.5E-15 "
            "solution_error= 3.7E-10",
            " HICAR terminal physical solve: iterations=21 "
            "relative_residual= 1.2E-11 status=0",
        ]
    )
    for _ in range(2 * hours):
        lines.extend(
            [
                " HICAR native FGMRES+line: iterations=8 "
                "true_residual= 3.0E-03 relative_residual= 3.0E-06 "
                "target= 1.0E-02",
                " HICAR BiCGStab status= 0 iterations= 8",
            ]
        )
    for _ in range(hours):
        lines.extend(
            [
                " HICAR adjoint conservation: relative_Bq= 8.0E-06 "
                "target= 2.0E-05",
                " time_step: 3.44 seconds",
            ]
        )
    lines.extend(
        ["Simulation completed successfully!", "Timing across all compute images:"]
    )
    return "\n".join(lines)


def test_valid_solver_log_passes():
    report = MODULE.evaluate(valid_log(), expected_hours=2)
    assert report["status"] == "PASS"
    assert report["solver"]["solve_count"] == 4
    assert report["exact_rap"]["level_count"] == 9
    assert report["exact_rap"]["declared_level_count"] == 9
    assert report["adjoint_conservation"]["failed_target_count"] == 0


def test_eight_level_hierarchy_passes():
    report = MODULE.evaluate(valid_log(rap_levels=8), expected_hours=2)
    assert report["status"] == "PASS"
    assert report["exact_rap"]["level_count"] == 8
    assert report["exact_rap"]["declared_level_count"] == 8


def test_hierarchy_declaration_must_match_verification_levels():
    report = MODULE.evaluate(
        valid_log(rap_levels=8, declared_rap_levels=9),
        expected_hours=2,
    )
    assert report["status"] == "FAIL"
    assert any(
        "hierarchy declared 9" in failure for failure in report["failures"]
    )


def test_failed_residual_and_missing_hour_are_detected():
    text = valid_log().replace(
        "true_residual= 3.0E-03", "true_residual= 3.0E-02", 1
    )
    report = MODULE.evaluate(text, expected_hours=3)
    assert report["status"] == "FAIL"
    assert report["solver"]["failed_target_count"] == 1
    assert any("completed model hours" in failure for failure in report["failures"])
