"""Certificate contract for states processed by HICAR's native operators."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np


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
    """Machine-checkable output of the future HICAR shared initialization core."""

    state_fingerprint: str
    pressure_operator: str
    wind_operator: str
    staggering: str
    maximum_discrete_hydrostatic_residual: float
    maximum_mass_continuity_residual: float
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
            maximum_mass_continuity_residual=float(payload["maximum_mass_continuity_residual"]),
            producer_commit=str(payload["producer_commit"]),
        )

    def validate(self, state: dict[str, np.ndarray]) -> None:
        if self.state_fingerprint != state_fingerprint(state):
            raise ValueError("balance certificate does not belong to this numerical state")
        if self.pressure_operator != "HICAR::domain_obj.adjust_pressure":
            raise ValueError("balance certificate used an unknown pressure operator")
        if self.wind_operator != "HICAR::wind.adjoint_variational_projection":
            raise ValueError("balance certificate used an unknown wind operator")
        if self.staggering != "HICAR_C_GRID_MASS_U_V_INTERFACE":
            raise ValueError("balance certificate does not certify exact HICAR staggering")
        for name, value in (
            ("hydrostatic", self.maximum_discrete_hydrostatic_residual),
            ("mass-continuity", self.maximum_mass_continuity_residual),
        ):
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"balance certificate has invalid {name} residual")
        if not self.producer_commit:
            raise ValueError("balance certificate lacks the HICAR producer commit")
