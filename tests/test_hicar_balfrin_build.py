from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = (
    ROOT
    / "case_studies"
    / "swiss_200m"
    / "scripts"
    / "build_hicar_balfrin.sbatch"
)
REFERENCE = (
    ROOT
    / ".agents"
    / "skills"
    / "hicar-balfrin-runtime"
    / "references"
    / "build-and-performance.md"
)
def test_canonical_builder_uses_cpu_partition_and_frozen_clean_source() -> None:
    text = BUILDER.read_text()

    assert "#SBATCH --partition=pp-short" in text
    assert "${HICAR_SOURCE_ROOT:?" in text
    assert "${HICAR_COORDINATOR_ROOT:?" in text
    assert "${HICAR_BUILD_ROOT:?" in text
    assert "${HICAR_EXPECTED_COMMIT:?" in text
    assert "test ! -e \"$build_root\"" in text
    assert "git -C \"$source_root\" diff --quiet" in text
    assert "git -C \"$source_root\" diff --cached --quiet" in text
    assert 'lock_dir="${source_root}.hicar-build-active"' in text
    assert 'if ! mkdir "$lock_dir"; then' in text
    assert '>"$lock_dir/owner"' in text
    assert "trap release_source_lock EXIT INT TERM" in text


def test_canonical_builder_separates_cpu_gpu_mpi_and_gpu_nccl() -> None:
    text = BUILDER.read_text()

    for variant in ("cpu)", "gpu-mpi|gpu-nccl)", "gpu-mpi", "gpu-nccl"):
        assert variant in text
    assert "gcc/12.3.0 cray-mpich-gcc/8.1.30" in text
    assert "nvhpc/24.5 cray-mpich-nvhpc/8.1.30" in text
    assert "openblas/0.3.26-gcc" in text
    assert "-DBLAS_LIBRARIES=" in text
    assert "-DLAPACK_LIBRARIES=" in text
    assert "openblas/0.3.21-nvhpc" not in text
    assert "-DOPENACC=OFF" in text
    assert "-DNCCL=OFF" in text
    assert "-DCMAKE_C_COMPILER=nvc" in text
    assert "-DCMAKE_CXX_COMPILER=nvc++" in text
    assert "cmake_args+=(-DNCCL=OFF)" in text
    assert "-DNCCL=ON" in text


def test_canonical_builder_requires_linkage_and_identity_evidence() -> None:
    text = BUILDER.read_text()

    assert "! ldd \"$executable\" | grep -q \"not found\"" in text
    assert 'ldd "$executable" | grep -q "libcufft"' in text
    assert 'ldd "$executable" | grep -q "libnccl"' in text
    assert 'sha256sum "$executable"' in text
    assert "--target HICAR HICAR-tester" in text
    assert 'tester="$build_root/tests/HICAR-tester"' in text
    assert 'sha256sum "$tester"' in text
    assert "hicar_build_provenance.txt" in text
    assert 'touch "$provenance.ready"' in text
    assert 'sha256sum "$(realpath "$0")"' in text
    assert "module -t list" in text


def test_runtime_reference_names_the_canonical_builder() -> None:
    text = REFERENCE.read_text()

    assert "build_hicar_balfrin.sbatch" in text
    assert "HICAR_BUILD_VARIANT=cpu" in text
    assert "HICAR_BUILD_VARIANT=gpu-mpi" in text
    assert "HICAR_BUILD_VARIANT=gpu-nccl" in text
    assert "Never run two builds concurrently against the same source clone" in text
