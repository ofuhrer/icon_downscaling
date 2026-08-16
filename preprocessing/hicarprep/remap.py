"""Reusable direct native-ICON to HICAR horizontal interpolation operators."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile

import netCDF4
import numpy as np
from scipy.spatial import cKDTree


EARTH_RADIUS_M = 6_371_000.0
RBF_SCALE_REFERENCE = 0.05
RBF_GRID_DISTANCE_REFERENCE_M = 13_000.0
MAXIMUM_RBF_CONDITION_NUMBER = 1.0e10
MAXIMUM_RBF_WEIGHT_AMPLIFICATION = 10.0
# An 80-level, ten-donor float64 gather is 200 MiB at this target count.
# Keeping this internal preserves the interpolation API while bounding both
# the gather and weighted-product temporaries on national target grids.
_RBF_APPLY_TARGET_CHUNK_SIZE = 32_768
RBF_APPLY_BACKENDS = ("numpy", "numba")


def coordinates_in_degrees(values: np.ndarray, units: str | None) -> np.ndarray:
    """Normalize angular coordinates while refusing unknown declared units."""
    values = np.asarray(values, dtype=np.float64)
    normalized = (units or "degrees").strip().lower()
    if normalized in {"degree", "degrees", "degrees_north", "degrees_east"}:
        return values
    if normalized in {"radian", "radians", "rad"}:
        return np.rad2deg(values)
    raise ValueError(f"unsupported angular coordinate units {units!r}")


def _unit_sphere(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    lat_r = np.deg2rad(np.asarray(lat, dtype=np.float64).ravel())
    lon_r = np.deg2rad(np.asarray(lon, dtype=np.float64).ravel())
    return np.column_stack(
        (np.cos(lat_r) * np.cos(lon_r), np.cos(lat_r) * np.sin(lon_r), np.sin(lat_r))
    )


def _tangent_basis(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat_r = np.deg2rad(np.asarray(lat, dtype=np.float64).ravel())
    lon_r = np.deg2rad(np.asarray(lon, dtype=np.float64).ravel())
    east = np.column_stack((-np.sin(lon_r), np.cos(lon_r), np.zeros_like(lon_r)))
    north = np.column_stack(
        (-np.sin(lat_r) * np.cos(lon_r), -np.sin(lat_r) * np.sin(lon_r), np.cos(lat_r))
    )
    return east, north


def _arc_distance(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Great-circle arc length in radians for Cartesian unit vectors."""
    return np.arccos(np.clip(np.asarray(left) @ np.asarray(right).T, -1.0, 1.0))


def _representative_grid_distance_m(source_xyz: np.ndarray) -> float:
    if source_xyz.shape[0] < 2:
        raise ValueError("an RBF source grid requires at least two points")
    chord, _ = cKDTree(source_xyz).query(source_xyz, k=2)
    arc = 2.0 * np.arcsin(np.clip(chord[:, 1] / 2.0, 0.0, 1.0))
    distance = float(np.median(arc) * EARTH_RADIUS_M)
    if not np.isfinite(distance) or distance <= 0.0:
        raise ValueError("source grid has no positive representative nearest-neighbour distance")
    return distance


def _rbf_scale_radians(source_xyz: np.ndarray, shape_factor: float) -> float:
    if not np.isfinite(shape_factor) or shape_factor <= 0.0:
        raise ValueError("RBF shape factor must be positive")
    return (
        RBF_SCALE_REFERENCE
        * _representative_grid_distance_m(source_xyz)
        / RBF_GRID_DISTANCE_REFERENCE_M
        * shape_factor
    )


def _solve_kernel(matrix: np.ndarray, rhs: np.ndarray) -> tuple[np.ndarray, float]:
    """Solve an RBF system with the smallest numerically conditioned nugget.

    A finite direct solve is not sufficient: nearly singular ICON stencils can
    otherwise produce normalized scalar weights of O(100), turning ordinary
    source winds into isolated O(300 m s-1) target-grid spikes.  Keep the
    smallest nugget whose 2-norm condition number is bounded before accepting
    the solution.
    """
    identity = np.eye(matrix.shape[0])
    for nugget in (0.0, 1.0e-14, 1.0e-12, 1.0e-10, 1.0e-8, 1.0e-7, 1.0e-6):
        regularized = matrix + nugget * identity
        condition_number = float(np.linalg.cond(regularized))
        if (
            not np.isfinite(condition_number)
            or condition_number > MAXIMUM_RBF_CONDITION_NUMBER
        ):
            continue
        try:
            coefficient = np.linalg.solve(regularized, rhs)
        except np.linalg.LinAlgError:
            continue
        if np.isfinite(coefficient).all():
            return coefficient, nugget
    raise ValueError(
        "RBF stencil kernel remains ill-conditioned after bounded regularization"
    )


def grid_fingerprint(lat: np.ndarray, lon: np.ndarray, *geometry: np.ndarray) -> str:
    digest = hashlib.sha256()
    for value in (lat, lon, *geometry):
        normalized = np.asarray(value, dtype="<f8")
        digest.update(str(normalized.size).encode())
        digest.update(normalized.tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class RBFWeights:
    donor_index: np.ndarray
    weight: np.ndarray
    target_shape: tuple[int, ...]
    source_fingerprint: str
    target_fingerprint: str
    scale_radians: float = np.nan
    maximum_nugget: float = 0.0
    method: str = "int2lm_gaussian_kernel_solve_nearest10_v2"

    def __post_init__(self) -> None:
        if self.donor_index.shape != self.weight.shape:
            raise ValueError("donor indices and weights must have identical shapes")
        if self.donor_index.ndim != 2:
            raise ValueError("donor indices and weights must be two-dimensional")
        if self.donor_index.shape[0] != int(np.prod(self.target_shape)):
            raise ValueError("weight target count does not match target shape")
        amplification = np.sum(np.abs(self.weight), axis=1)
        if not np.isfinite(self.weight).all() or np.any(
            amplification > MAXIMUM_RBF_WEIGHT_AMPLIFICATION
        ):
            raise ValueError(
                "RBF weights exceed the bounded interpolation amplification"
            )
        if not np.allclose(np.sum(self.weight, axis=1), 1.0, atol=2.0e-7):
            raise ValueError("horizontal weights do not preserve constants")

    def apply(
        self,
        source: np.ndarray,
        *,
        monotone: bool = False,
        backend: str = "numpy",
    ) -> np.ndarray:
        """Apply weights to an array whose final dimension is native cell."""
        source = np.asanyarray(source)
        if source.ndim == 0:
            raise ValueError("source must have at least one dimension")
        if source.shape[-1] <= int(np.max(self.donor_index)):
            raise ValueError("source cell dimension is shorter than cached donor index")
        if backend not in RBF_APPLY_BACKENDS:
            raise ValueError(
                f"unsupported RBF apply backend {backend!r}; expected one of "
                + ", ".join(RBF_APPLY_BACKENDS)
            )
        if backend == "numba":
            if np.ma.isMaskedArray(source):
                raise ValueError("the numba RBF backend requires an unmasked source array")
            try:
                from .accelerated_rbf import apply_rbf
            except ImportError as exc:
                raise RuntimeError(
                    "the numba RBF backend requires the optional numba dependency"
                ) from exc
            result = apply_rbf(
                source,
                self.donor_index,
                self.weight,
                monotone=monotone,
            )
            return result.reshape((*source.shape[:-1], *self.target_shape))

        target_count = self.donor_index.shape[0]
        result = None
        for start in range(0, target_count, _RBF_APPLY_TARGET_CHUNK_SIZE):
            stop = min(start + _RBF_APPLY_TARGET_CHUNK_SIZE, target_count)
            donors = np.take(source, self.donor_index[start:stop], axis=-1)
            if monotone:
                lower = np.min(donors, axis=-1)
                upper = np.max(donors, axis=-1)
            product_dtype = np.result_type(donors.dtype, self.weight.dtype)
            product_output = (
                donors
                if np.can_cast(product_dtype, donors.dtype, casting="same_kind")
                else None
            )
            product = np.multiply(
                donors,
                self.weight[start:stop],
                out=product_output,
            )
            chunk = np.sum(product, axis=-1)
            if monotone:
                np.clip(chunk, lower, upper, out=chunk)
            if result is None:
                result_shape = (*source.shape[:-1], target_count)
                if np.ma.isMaskedArray(chunk):
                    result = np.ma.empty(result_shape, dtype=chunk.dtype)
                else:
                    result = np.empty(result_shape, dtype=chunk.dtype)
            result[..., start:stop] = chunk
            del donors, product, chunk
        if result is None:
            raise ValueError("cached RBF operator has no target points")
        return result.reshape((*source.shape[:-1], *self.target_shape))

    def apply_same_surface(
        self,
        source: np.ndarray,
        source_support: np.ndarray,
        target_support: np.ndarray,
    ) -> tuple[np.ndarray, int]:
        """Normalize over donors on the target surface; report nearest-donor fallbacks."""
        source = np.asarray(source)
        support = np.asarray(source_support).astype(bool)
        target_support_flat = np.asarray(target_support).astype(bool).ravel()
        donor_support = support[self.donor_index]
        eligible = donor_support == target_support_flat[:, None]
        local_weight = np.where(eligible, self.weight, 0.0)
        total = np.sum(local_weight, axis=1)
        fallback = np.abs(total) <= 1.0e-14
        local_weight[~fallback] /= total[~fallback, None]
        if np.any(fallback):
            local_weight[fallback] = 0.0
            local_weight[fallback, 0] = 1.0
        donors = np.take(source, self.donor_index, axis=-1)
        result = np.sum(donors * local_weight, axis=-1)
        return result.reshape((*source.shape[:-1], *self.target_shape)), int(np.sum(fallback))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".partial", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with netCDF4.Dataset(temporary, "w") as dataset:
                dataset.createDimension("target_point", self.donor_index.shape[0])
                dataset.createDimension("donor", self.donor_index.shape[1])
                dataset.createDimension("target_rank", len(self.target_shape))
                dataset.createVariable("donor_index", "i8", ("target_point", "donor"))[:] = (
                    self.donor_index
                )
                dataset.createVariable("weight", "f8", ("target_point", "donor"))[:] = self.weight
                dataset.createVariable("target_shape", "i8", ("target_rank",))[:] = (
                    self.target_shape
                )
                dataset.method = self.method
                dataset.source_grid_fingerprint = self.source_fingerprint
                dataset.target_grid_fingerprint = self.target_fingerprint
                dataset.rbf_scale_radians = self.scale_radians
                dataset.maximum_diagonal_nugget = self.maximum_nugget
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def read(cls, path: Path) -> "RBFWeights":
        with netCDF4.Dataset(path) as dataset:
            return cls(
                donor_index=np.asarray(dataset["donor_index"][:], dtype=np.int64),
                weight=np.asarray(dataset["weight"][:], dtype=np.float64),
                target_shape=tuple(int(v) for v in dataset["target_shape"][:]),
                source_fingerprint=str(dataset.source_grid_fingerprint),
                target_fingerprint=str(dataset.target_grid_fingerprint),
                scale_radians=float(getattr(dataset, "rbf_scale_radians", np.nan)),
                maximum_nugget=float(getattr(dataset, "maximum_diagonal_nugget", 0.0)),
                method=str(dataset.method),
            )


@dataclass(frozen=True)
class VectorRBFWeights:
    donor_index: np.ndarray
    east_weight: np.ndarray
    north_weight: np.ndarray
    target_shape: tuple[int, ...]
    source_fingerprint: str
    target_fingerprint: str
    scale_radians: float
    maximum_nugget: float = 0.0
    method: str = "icon_int2lm_vector_gaussian_kernel_solve_nearest9_v1"

    def __post_init__(self) -> None:
        if not (self.donor_index.shape == self.east_weight.shape == self.north_weight.shape):
            raise ValueError(
                "vector donor indices and component weights must have identical shapes"
            )
        if self.donor_index.shape[0] != int(np.prod(self.target_shape)):
            raise ValueError("vector weight target count does not match target shape")

    def apply(self, normal_velocity: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        normal_velocity = np.asarray(normal_velocity)
        if normal_velocity.shape[-1] <= int(np.max(self.donor_index)):
            raise ValueError("VN edge dimension is shorter than cached donor index")
        sampled = np.take(normal_velocity, self.donor_index, axis=-1)
        u = np.sum(sampled * self.east_weight, axis=-1)
        v = np.sum(sampled * self.north_weight, axis=-1)
        shape = (*normal_velocity.shape[:-1], *self.target_shape)
        return u.reshape(shape), v.reshape(shape)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".partial", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with netCDF4.Dataset(temporary, "w") as dataset:
                dataset.createDimension("target_point", self.donor_index.shape[0])
                dataset.createDimension("donor", self.donor_index.shape[1])
                dataset.createDimension("target_rank", len(self.target_shape))
                dataset.createVariable("donor_index", "i8", ("target_point", "donor"))[:] = (
                    self.donor_index
                )
                dataset.createVariable("east_weight", "f8", ("target_point", "donor"))[:] = (
                    self.east_weight
                )
                dataset.createVariable("north_weight", "f8", ("target_point", "donor"))[:] = (
                    self.north_weight
                )
                dataset.createVariable("target_shape", "i8", ("target_rank",))[:] = (
                    self.target_shape
                )
                dataset.method = self.method
                dataset.source_grid_fingerprint = self.source_fingerprint
                dataset.target_grid_fingerprint = self.target_fingerprint
                dataset.rbf_scale_radians = self.scale_radians
                dataset.maximum_diagonal_nugget = self.maximum_nugget
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def read(cls, path: Path) -> "VectorRBFWeights":
        with netCDF4.Dataset(path) as dataset:
            return cls(
                donor_index=np.asarray(dataset["donor_index"][:], dtype=np.int64),
                east_weight=np.asarray(dataset["east_weight"][:], dtype=np.float64),
                north_weight=np.asarray(dataset["north_weight"][:], dtype=np.float64),
                target_shape=tuple(int(v) for v in dataset["target_shape"][:]),
                source_fingerprint=str(dataset.source_grid_fingerprint),
                target_fingerprint=str(dataset.target_grid_fingerprint),
                scale_radians=float(dataset.rbf_scale_radians),
                maximum_nugget=float(getattr(dataset, "maximum_diagonal_nugget", 0.0)),
                method=str(dataset.method),
            )


def build_rbf_weights(
    source_lat: np.ndarray,
    source_lon: np.ndarray,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    *,
    donors: int = 10,
    shape_factor: float = 1.0,
    maximum_distance_factor: float = 3.0,
) -> RBFWeights:
    """Build the normalized Gaussian kernel solve used by int2lm for scalars."""
    if donors < 1:
        raise ValueError("at least one donor is required")
    source_xyz = _unit_sphere(source_lat, source_lon)
    target_xyz = _unit_sphere(target_lat, target_lon)
    if donors > source_xyz.shape[0]:
        raise ValueError("donor count exceeds source grid size")
    chord, index = cKDTree(source_xyz).query(target_xyz, k=donors)
    if donors == 1:
        chord = chord[:, None]
        index = index[:, None]
    grid_distance_m = _representative_grid_distance_m(source_xyz)
    nearest_distance_m = 2.0 * np.arcsin(np.clip(chord[:, 0] / 2.0, 0.0, 1.0)) * EARTH_RADIUS_M
    if maximum_distance_factor <= 0.0 or np.any(
        nearest_distance_m > maximum_distance_factor * grid_distance_m
    ):
        raise ValueError("target grid is not covered by the padded ICON source grid")
    scale = _rbf_scale_radians(source_xyz, shape_factor)
    weight = np.empty(index.shape, dtype=np.float64)
    maximum_nugget = 0.0
    for point, stencil in enumerate(index):
        donor_xyz = source_xyz[stencil]
        kernel = np.exp(-np.square(_arc_distance(donor_xyz, donor_xyz) / scale))
        rhs = np.exp(-np.square(_arc_distance(target_xyz[point : point + 1], donor_xyz)[0] / scale))
        coefficient, nugget = _solve_kernel(kernel, rhs)
        checksum = float(np.sum(coefficient))
        if not np.isfinite(checksum) or abs(checksum) <= 1.0e-14:
            raise ValueError("scalar RBF normalization checksum is singular")
        weight[point] = coefficient / checksum
        maximum_nugget = max(maximum_nugget, nugget)
    return RBFWeights(
        donor_index=np.asarray(index, dtype=np.int64),
        weight=weight,
        target_shape=np.asarray(target_lat).shape,
        source_fingerprint=grid_fingerprint(source_lat, source_lon),
        target_fingerprint=grid_fingerprint(target_lat, target_lon),
        scale_radians=scale,
        maximum_nugget=maximum_nugget,
    )


def build_vector_rbf_weights(
    source_lat: np.ndarray,
    source_lon: np.ndarray,
    normal_east: np.ndarray,
    normal_north: np.ndarray,
    target_lat: np.ndarray,
    target_lon: np.ndarray,
    *,
    donors: int = 9,
    shape_factor: float = 1.0,
    maximum_distance_factor: float = 3.0,
) -> VectorRBFWeights:
    """Build ICON/int2lm vector RBF coefficients for native edge-normal wind."""
    source_xyz = _unit_sphere(source_lat, source_lon)
    target_xyz = _unit_sphere(target_lat, target_lon)
    normal_east = np.asarray(normal_east, dtype=np.float64).ravel()
    normal_north = np.asarray(normal_north, dtype=np.float64).ravel()
    if source_xyz.shape[0] != normal_east.size or source_xyz.shape[0] != normal_north.size:
        raise ValueError("edge coordinates and edge-normal geometry are inconsistent")
    if donors < 2 or donors > source_xyz.shape[0]:
        raise ValueError("vector RBF donor count must be between two and the source edge count")
    source_east, source_north = _tangent_basis(source_lat, source_lon)
    normal = normal_east[:, None] * source_east + normal_north[:, None] * source_north
    norm = np.linalg.norm(normal, axis=1)
    if np.any(norm <= 1.0e-12):
        raise ValueError("source edge contains a zero normal vector")
    normal /= norm[:, None]
    target_east, target_north = _tangent_basis(target_lat, target_lon)
    chord, index = cKDTree(source_xyz).query(target_xyz, k=donors)
    grid_distance_m = _representative_grid_distance_m(source_xyz)
    nearest_distance_m = 2.0 * np.arcsin(np.clip(chord[:, 0] / 2.0, 0.0, 1.0)) * EARTH_RADIUS_M
    if maximum_distance_factor <= 0.0 or np.any(
        nearest_distance_m > maximum_distance_factor * grid_distance_m
    ):
        raise ValueError("target grid is not covered by the padded ICON edge grid")
    scale = _rbf_scale_radians(source_xyz, shape_factor)
    east_weight = np.empty(index.shape, dtype=np.float64)
    north_weight = np.empty(index.shape, dtype=np.float64)
    maximum_nugget = 0.0
    for point, stencil in enumerate(index):
        donor_xyz = source_xyz[stencil]
        donor_normal = normal[stencil]
        gaussian_pair = np.exp(-np.square(_arc_distance(donor_xyz, donor_xyz) / scale))
        kernel = (donor_normal @ donor_normal.T) * gaussian_pair
        gaussian_target = np.exp(
            -np.square(_arc_distance(target_xyz[point : point + 1], donor_xyz)[0] / scale)
        )
        rhs_east = gaussian_target * (donor_normal @ target_east[point])
        rhs_north = gaussian_target * (donor_normal @ target_north[point])
        coefficient_east, nugget_east = _solve_kernel(kernel, rhs_east)
        coefficient_north, nugget_north = _solve_kernel(kernel, rhs_north)
        checksum_east = float(np.sum(coefficient_east * (donor_normal @ target_east[point])))
        checksum_north = float(np.sum(coefficient_north * (donor_normal @ target_north[point])))
        if abs(checksum_east) <= 1.0e-14 or abs(checksum_north) <= 1.0e-14:
            raise ValueError("vector RBF normalization checksum is singular")
        east_weight[point] = coefficient_east / checksum_east
        north_weight[point] = coefficient_north / checksum_north
        maximum_nugget = max(maximum_nugget, nugget_east, nugget_north)
    return VectorRBFWeights(
        donor_index=np.asarray(index, dtype=np.int64),
        east_weight=east_weight,
        north_weight=north_weight,
        target_shape=np.asarray(target_lat).shape,
        source_fingerprint=grid_fingerprint(source_lat, source_lon, normal_east, normal_north),
        target_fingerprint=grid_fingerprint(target_lat, target_lon),
        scale_radians=scale,
        maximum_nugget=maximum_nugget,
    )


def reconstruct_vector_from_normals(
    normal_velocity: np.ndarray, operator: VectorRBFWeights
) -> tuple[np.ndarray, np.ndarray]:
    """Recover earth-relative U/V using cached ICON/int2lm vector RBF coefficients."""
    return operator.apply(normal_velocity)
