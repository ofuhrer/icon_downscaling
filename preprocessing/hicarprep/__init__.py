"""Target-model-aware ICON to HICAR preprocessing."""

from .geometry import SleveConfig, build_sleve_geometry
from .products import assemble_hicar_runtime_domain, validate_hicar_runtime_domain
from .registry import FieldLifetime, FieldRegistry
from .remap import VectorRBFWeights, RBFWeights, build_rbf_weights, build_vector_rbf_weights
from .rotation import earth_to_grid_wind, grid_to_earth_wind, hicar_grid_rotation
from .surface import prepare_surface_state
from .vertical import (
    adjust_vertical_velocity,
    interpolate_interface_w_to_hfl,
    reconstruct_column_state,
)

__all__ = [
    "FieldLifetime",
    "FieldRegistry",
    "RBFWeights",
    "VectorRBFWeights",
    "SleveConfig",
    "build_rbf_weights",
    "build_vector_rbf_weights",
    "build_sleve_geometry",
    "hicar_grid_rotation",
    "earth_to_grid_wind",
    "grid_to_earth_wind",
    "reconstruct_column_state",
    "adjust_vertical_velocity",
    "interpolate_interface_w_to_hfl",
    "prepare_surface_state",
    "assemble_hicar_runtime_domain",
    "validate_hicar_runtime_domain",
]
