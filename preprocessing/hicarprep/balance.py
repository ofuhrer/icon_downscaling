"""Certificate contract for states processed by HICAR's native operators."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np


PRESSURE_OPERATOR = "HICAR::domain_obj.adjust_pressure_temp"
WIND_OPERATOR = "HICAR::wind.adjoint_variational_projection"
STAGGERING = "HICAR_C_GRID_U_V_W_NATIVE"
WIND_MATRIX_RELATIVE_TOLERANCE = 1.0e-5
MASS_CONTINUITY_RELATIVE_TOLERANCE = 2.0e-5

_HICAR_VARIABLES = {
    "T": "temperature",
    "P": "pressure",
    "QV": "qv",
    "QC": "qc",
    "QI": "qi",
    "U": "u",
    "V": "v",
    "W": "w_grid",
    "THETA": "potential_temperature",
    "RHO": "density",
    "HFL": "z",
    "HHL": "z_i",
    "lat": "lat",
    "lon": "lon",
}
_OPTIONAL_HICAR_VARIABLES = {
    "QR": "qr",
    "QS": "qs",
    "QG": "qg",
}


def state_fingerprint(state: dict[str, np.ndarray]) -> str:
    """Hash the complete numerical state, including names, shapes, and values."""
    digest = hashlib.sha256()
    for name in sorted(state):
        values = np.asarray(state[name], dtype="<f8")
        digest.update(name.encode())
        digest.update(str(values.shape).encode())
        digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class BalanceCertificate:
    """Machine-checkable evidence from HICAR's shared initialization core."""

    state_fingerprint: str
    pressure_operator: str
    wind_operator: str
    staggering: str
    maximum_discrete_hydrostatic_residual: float
    hydrostatic_residual_tolerance: float
    maximum_wind_matrix_relative_residual: float
    maximum_mass_continuity_residual: float
    valid_time: str
    producer_commit: str

    @classmethod
    def from_json(cls, path: Path) -> "BalanceCertificate":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "hicar-balance-certificate-v1":
            raise ValueError("unknown HICAR balance-certificate schema")
        return cls(
            state_fingerprint=str(payload["state_fingerprint"]),
            pressure_operator=str(payload["pressure_operator"]),
            wind_operator=str(payload["wind_operator"]),
            staggering=str(payload["staggering"]),
            maximum_discrete_hydrostatic_residual=float(
                payload["maximum_discrete_hydrostatic_residual"]
            ),
            hydrostatic_residual_tolerance=float(payload["hydrostatic_residual_tolerance"]),
            maximum_wind_matrix_relative_residual=float(
                payload["maximum_wind_matrix_relative_residual"]
            ),
            maximum_mass_continuity_residual=float(payload["maximum_mass_continuity_residual"]),
            valid_time=str(payload["valid_time"]),
            producer_commit=str(payload["producer_commit"]),
        )

    def to_json(self, path: Path) -> None:
        payload = {"schema": "hicar-balance-certificate-v1", **self.__dict__}
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".partial", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def validate(
        self,
        state: dict[str, np.ndarray],
        *,
        valid_time: str | None = None,
        producer_commit: str | None = None,
    ) -> None:
        if self.state_fingerprint != state_fingerprint(state):
            raise ValueError("balance certificate does not belong to this numerical state")
        if self.pressure_operator != PRESSURE_OPERATOR:
            raise ValueError("balance certificate used an unknown pressure operator")
        if self.wind_operator != WIND_OPERATOR:
            raise ValueError("balance certificate used an unknown wind operator")
        if self.staggering != STAGGERING:
            raise ValueError("balance certificate does not certify exact HICAR staggering")
        for name, value in (
            ("hydrostatic", self.maximum_discrete_hydrostatic_residual),
            ("hydrostatic tolerance", self.hydrostatic_residual_tolerance),
            ("wind-matrix", self.maximum_wind_matrix_relative_residual),
            ("mass-continuity", self.maximum_mass_continuity_residual),
        ):
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"balance certificate has invalid {name} residual")
        if self.hydrostatic_residual_tolerance <= 0.0:
            raise ValueError("balance certificate has invalid hydrostatic residual tolerance")
        if self.maximum_discrete_hydrostatic_residual > self.hydrostatic_residual_tolerance:
            raise ValueError("balance certificate exceeds its hydrostatic residual tolerance")
        if self.maximum_wind_matrix_relative_residual > WIND_MATRIX_RELATIVE_TOLERANCE:
            raise ValueError("balance certificate exceeds the wind-matrix residual tolerance")
        if self.maximum_mass_continuity_residual > MASS_CONTINUITY_RELATIVE_TOLERANCE:
            raise ValueError("balance certificate exceeds the mass-continuity residual tolerance")
        if not self.valid_time:
            raise ValueError("balance certificate lacks the initialized-state valid time")
        if valid_time is not None and self.valid_time != valid_time:
            raise ValueError("balance certificate has a different initialized-state valid time")
        if not self.producer_commit:
            raise ValueError("balance certificate lacks the HICAR producer commit")
        if producer_commit is not None and not (
            self.producer_commit.startswith(producer_commit)
            or producer_commit.startswith(self.producer_commit)
        ):
            raise ValueError("balance certificate has a different HICAR producer commit")


def _canonical_array(variable: netCDF4.Variable) -> np.ndarray:
    values = np.asarray(np.ma.asarray(variable[:]).filled(np.nan), dtype=np.float64)
    dimensions = list(variable.dimensions)
    if "time" in dimensions:
        axis = dimensions.index("time")
        if values.shape[axis] != 1:
            raise ValueError(f"{variable.name}: initialized state must contain one time")
        values = np.take(values, 0, axis=axis)
        dimensions.pop(axis)

    vertical = next((name for name in ("level", "level_i") if name in dimensions), None)
    y_dimension = next((name for name in ("lat_y", "lat_v") if name in dimensions), None)
    x_dimension = next((name for name in ("lon_x", "lon_u") if name in dimensions), None)
    ordered = [name for name in (vertical, y_dimension, x_dimension) if name is not None]
    if set(dimensions) != set(ordered):
        raise ValueError(f"{variable.name}: unsupported HICAR dimensions {tuple(dimensions)}")
    if ordered:
        values = np.transpose(values, tuple(dimensions.index(name) for name in ordered))
    return values


def load_hicar_initialized_state(path: Path) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    """Load time-zero HICAR output into canonical level/y/x array order."""
    state: dict[str, np.ndarray] = {}
    with netCDF4.Dataset(path) as dataset:
        for canonical, source in _HICAR_VARIABLES.items():
            if source not in dataset.variables:
                raise ValueError(f"{path}: HICAR initialization output lacks {source!r}")
            state[canonical] = _canonical_array(dataset[source])
        for canonical, source in _OPTIONAL_HICAR_VARIABLES.items():
            if source in dataset.variables:
                state[canonical] = _canonical_array(dataset[source])
        valid_time = str(
            getattr(dataset, "valid_time", getattr(dataset, "model_initialization_time", ""))
        )
        if not valid_time and "time" in dataset.variables:
            time_variable = dataset["time"]
            if time_variable.size != 1 or not getattr(time_variable, "units", ""):
                raise ValueError(f"{path}: HICAR initialized state has an ambiguous time axis")
            decoded = netCDF4.num2date(
                np.asarray(time_variable[:]).reshape(-1)[0],
                units=time_variable.units,
                calendar=getattr(time_variable, "calendar", "standard"),
            )
            decoded_time = dt.datetime(
                decoded.year,
                decoded.month,
                decoded.day,
                decoded.hour,
                decoded.minute,
                decoded.second,
                decoded.microsecond,
                tzinfo=dt.timezone.utc,
            )
            rounded_time = decoded_time.replace(microsecond=0)
            if decoded_time.microsecond >= 500_000:
                rounded_time += dt.timedelta(seconds=1)
            valid_time = rounded_time.isoformat().replace("+00:00", "Z")
        output_commit = str(getattr(dataset, "git_tag", "")).strip()
        if not output_commit:
            output_commit = str(getattr(dataset, "git", "")).strip()
        metadata = {"valid_time": valid_time, "hicar_git": output_commit}
    _validate_native_staggering(state)
    return state, metadata


def _validate_native_staggering(state: dict[str, np.ndarray]) -> None:
    mass_shape = state["T"].shape
    if len(mass_shape) != 3:
        raise ValueError("HICAR mass fields must have canonical (level, y, x) dimensions")
    levels, ny, nx = mass_shape
    expected = {
        "T": mass_shape,
        "P": mass_shape,
        "QV": mass_shape,
        "QC": mass_shape,
        "QI": mass_shape,
        "THETA": mass_shape,
        "RHO": mass_shape,
        "HFL": mass_shape,
        "U": (levels, ny, nx + 1),
        "V": (levels, ny + 1, nx),
        "W": mass_shape,
        "HHL": (levels + 1, ny, nx),
        "lat": (ny, nx),
        "lon": (ny, nx),
    }
    expected.update(
        {name: mass_shape for name in ("QR", "QS", "QG") if name in state}
    )
    for name, shape in expected.items():
        if state[name].shape != shape:
            raise ValueError(
                f"{name}: expected exact HICAR native shape {shape}, got {state[name].shape}"
            )
        if not np.isfinite(state[name]).all():
            raise ValueError(f"{name}: HICAR initialized state contains non-finite values")
    for name in ("T", "P", "THETA", "RHO"):
        if np.any(state[name] <= 0.0):
            raise ValueError(f"{name}: HICAR initialized state must be positive")
    for name in ("QV", "QC", "QI", "QR", "QS", "QG"):
        if name in state and np.any(state[name] < 0.0):
            raise ValueError(f"{name}: HICAR initialized water species must be nonnegative")
    if np.any(np.diff(state["HHL"], axis=0) <= 0.0) or np.any(
        np.diff(state["HFL"], axis=0) <= 0.0
    ):
        raise ValueError("HICAR initialized vertical geometry is not strictly increasing")
    if np.any(state["HFL"] <= state["HHL"][:-1]) or np.any(
        state["HFL"] >= state["HHL"][1:]
    ):
        raise ValueError("HICAR full levels must lie strictly between their interfaces")
    if np.any(np.diff(state["P"], axis=0) >= 0.0):
        raise ValueError("HICAR initialized pressure must decrease with height")
    if np.any(np.abs(state["lat"]) > 90.0):
        raise ValueError("HICAR initialized latitude is outside [-90, 90]")


def maximum_discrete_hydrostatic_residual(state: dict[str, np.ndarray]) -> float:
    """Return max residual of a layer-centred discrete moist hydrostatic relation."""
    pressure = state["P"]
    temperature = state["T"]
    qv = state["QV"]
    height = state["HFL"]
    if np.any(pressure <= 0.0) or np.any(temperature <= 0.0):
        raise ValueError("pressure and temperature must be positive for hydrostatic validation")
    epsilon = 287.05 / 461.5
    if np.any(qv < 0.0):
        raise ValueError("QV must be nonnegative for hydrostatic validation")
    total_water = qv.copy()
    for name in ("QC", "QI", "QR", "QS", "QG"):
        if name in state:
            if np.any(state[name] < 0.0):
                raise ValueError(f"{name} must be nonnegative for hydrostatic validation")
            total_water += state[name]
    virtual_temperature = temperature * (1.0 + qv / epsilon) / (1.0 + total_water)
    layer_mean = 0.5 * (virtual_temperature[1:] + virtual_temperature[:-1])
    residual = np.log(pressure[1:] / pressure[:-1]) + (
        9.80665 * (height[1:] - height[:-1]) / (287.05 * layer_mean)
    )
    return float(np.max(np.abs(residual)))


def issue_balance_certificate(
    state_path: Path,
    diagnostics_path: Path,
    *,
    maximum_hydrostatic_residual: float,
) -> BalanceCertificate:
    """Validate HICAR time-zero output and issue a state-bound certificate."""
    if maximum_hydrostatic_residual <= 0.0:
        raise ValueError("hydrostatic residual tolerance must be positive")
    state, metadata = load_hicar_initialized_state(state_path)
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    if diagnostics.get("schema") != "hicar-initialization-diagnostics-v1":
        raise ValueError("unknown HICAR initialization-diagnostics schema")
    if diagnostics.get("pressure_operator") != PRESSURE_OPERATOR:
        raise ValueError("HICAR diagnostics report an unexpected pressure operator")
    if diagnostics.get("wind_operator") != WIND_OPERATOR:
        raise ValueError("HICAR diagnostics report an unexpected wind operator")
    if diagnostics.get("staggering") != STAGGERING:
        raise ValueError("HICAR diagnostics report unexpected staggering")
    matrix_initial = float(diagnostics["wind_matrix_initial_residual"])
    matrix_final = float(diagnostics["wind_matrix_final_residual"])
    matrix_relative = float(diagnostics["wind_matrix_relative_residual"])
    continuity_initial = float(diagnostics["mass_continuity_initial_norm2"])
    continuity_final = float(diagnostics["mass_continuity_final_norm2"])
    continuity_relative = float(diagnostics["mass_continuity_relative_residual"])
    for name, value in (
        ("wind matrix initial", matrix_initial),
        ("wind matrix final", matrix_final),
        ("mass continuity initial", continuity_initial),
        ("mass continuity final", continuity_final),
    ):
        if not np.isfinite(value) or value < 0.0:
            raise ValueError(f"HICAR diagnostics have invalid {name} residual")
    expected_matrix_relative = matrix_final / max(matrix_initial, np.finfo(float).tiny)
    expected_continuity_relative = np.sqrt(
        continuity_final / max(continuity_initial, np.finfo(float).tiny)
    )
    if not np.isclose(matrix_relative, expected_matrix_relative, rtol=1.0e-12, atol=0.0):
        raise ValueError("HICAR wind-matrix diagnostics are internally inconsistent")
    if not np.isclose(
        continuity_relative, expected_continuity_relative, rtol=1.0e-12, atol=0.0
    ):
        raise ValueError("HICAR mass-continuity diagnostics are internally inconsistent")
    if int(diagnostics["wind_solver_status"]) != 0:
        raise ValueError("HICAR wind projection did not converge")
    if int(diagnostics["wind_solver_iterations"]) < 0:
        raise ValueError("HICAR diagnostics have an invalid wind-solver iteration count")
    if not np.isfinite(matrix_relative) or matrix_relative > WIND_MATRIX_RELATIVE_TOLERANCE:
        raise ValueError("HICAR wind matrix residual exceeds the certification tolerance")
    if (
        not np.isfinite(continuity_relative)
        or continuity_relative < 0.0
        or continuity_relative > MASS_CONTINUITY_RELATIVE_TOLERANCE
    ):
        raise ValueError("HICAR mass-continuity residual exceeds the certification tolerance")
    if diagnostics.get("passed") is not True:
        raise ValueError("HICAR initialization diagnostics did not pass")
    producer_commit = str(diagnostics.get("producer_commit", "")).strip()
    output_commit = metadata["hicar_git"].strip()
    if not producer_commit:
        raise ValueError("HICAR initialization diagnostics lack the producer commit")
    if not output_commit:
        raise ValueError("HICAR initialized state lacks the producer commit")
    if not (producer_commit.startswith(output_commit) or output_commit.startswith(producer_commit)):
        raise ValueError("HICAR diagnostics and initialized state have different producer commits")
    hydrostatic_residual = maximum_discrete_hydrostatic_residual(state)
    if hydrostatic_residual > maximum_hydrostatic_residual:
        raise ValueError(
            "HICAR initialized pressure fails the discrete hydrostatic gate: "
            f"{hydrostatic_residual:.6g} > {maximum_hydrostatic_residual:.6g}"
        )
    certificate = BalanceCertificate(
        state_fingerprint=state_fingerprint(state),
        pressure_operator=PRESSURE_OPERATOR,
        wind_operator=WIND_OPERATOR,
        staggering=STAGGERING,
        maximum_discrete_hydrostatic_residual=hydrostatic_residual,
        hydrostatic_residual_tolerance=maximum_hydrostatic_residual,
        maximum_wind_matrix_relative_residual=matrix_relative,
        maximum_mass_continuity_residual=continuity_relative,
        valid_time=metadata["valid_time"],
        producer_commit=producer_commit,
    )
    certificate.validate(
        state, valid_time=metadata["valid_time"], producer_commit=metadata["hicar_git"]
    )
    return certificate
