from __future__ import annotations

import ctypes
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..hashing import sha256_text
from ..models import (
    BenchmarkResult,
    BenchmarkStage,
    BenchmarkStatus,
    BenchmarkTiming,
    BuildExecution,
    BuildResult,
    BuildStatus,
    Candidate,
    CandidateOrigin,
    CandidateRepresentation,
    CompileConfig,
    Descriptor,
    LaunchConfig,
    SourceFile,
    StaticCheckResult,
    ValidationFailureCase,
    ValidationResult,
    ValidationStatus,
    ValidationTolerance,
)
from ..sdk import OptimizationProblem, ProblemRunContext
from ..stats import summarize


@dataclass(slots=True)
class _PythonSimRuntime:
    unroll: int
    vec_width: int

    def run(self, a: list[float], b: list[float]) -> list[float]:
        out = [0.0] * len(a)
        step = max(1, self.unroll * self.vec_width)
        for i in range(0, len(a), step):
            upper = min(len(a), i + step)
            j = i
            while j < upper:
                out[j] = a[j] + b[j]
                j += 1
        return out

    def benchmark(self, n: int, warmup: int, iters: int) -> list[float]:
        rng = random.Random(17)
        a = [rng.uniform(-1.0, 1.0) for _ in range(n)]
        b = [rng.uniform(-1.0, 1.0) for _ in range(n)]

        for _ in range(warmup):
            self.run(a, b)

        samples_us: list[float] = []
        for _ in range(iters):
            start_ns = time.perf_counter_ns()
            self.run(a, b)
            end_ns = time.perf_counter_ns()
            samples_us.append((end_ns - start_ns) / 1_000.0)
        return samples_us


class _NvccRuntime:
    def __init__(self, library_path: Path, block_size: int) -> None:
        self.library_path = library_path
        self.block_size = block_size
        self._lib = ctypes.CDLL(str(library_path))

        self._run = self._lib.vector_add_run
        self._run.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._run.restype = ctypes.c_int

        self._bench = self._lib.vector_add_benchmark
        self._bench.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_float),
        ]
        self._bench.restype = ctypes.c_int

    def run(self, a: list[float], b: list[float]) -> list[float]:
        n = len(a)
        arr_a = (ctypes.c_float * n)(*a)
        arr_b = (ctypes.c_float * n)(*b)
        arr_out = (ctypes.c_float * n)()

        status = int(self._run(arr_a, arr_b, arr_out, n, self.block_size))
        if status != 0:
            raise RuntimeError(f"vector_add_run failed with cuda status={status}")
        return list(arr_out)

    def benchmark(self, n: int, warmup: int, iters: int) -> list[float]:
        out = (ctypes.c_float * iters)()
        status = int(self._bench(n, self.block_size, warmup, iters, out))
        if status != 0:
            raise RuntimeError(f"vector_add_benchmark failed with cuda status={status}")
        return [float(out[i]) for i in range(iters)]


@dataclass(slots=True)
class VectorAddConfig:
    backend: str = "python-sim"
    default_arch: str = "auto"
    quick_size: int = 20_000
    full_size: int = 100_000
    quick_warmup: int = 3
    quick_iters: int = 15
    full_warmup: int = 6
    full_iters: int = 40
    validation_size: int = 8_192
    seed_count: int = 4
    tolerance_rtol: float = 1e-7
    tolerance_atol: float = 1e-7
    default_block_size: int = 256

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "VectorAddConfig":
        if not data:
            return VectorAddConfig()
        allowed = {field.name for field in VectorAddConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in allowed}
        return VectorAddConfig(**filtered)


class VectorAddProblem(OptimizationProblem):
    def __init__(self, config: VectorAddConfig | None = None) -> None:
        self.config = config or VectorAddConfig()

    @classmethod
    def from_config_dict(cls, data: dict[str, Any] | None) -> "VectorAddProblem":
        return cls(VectorAddConfig.from_dict(data))

    def to_config_dict(self) -> dict[str, Any]:
        return self.config.to_dict()

    def problem_id(self) -> str:
        return "vector_add_v1"

    def baseline(self, ctx: ProblemRunContext) -> Candidate | None:
        return self._make_candidate(
            run_id=ctx.run_id,
            params={"unroll": 1, "vec_width": 1},
            operation="seed",
            agent_id="baseline",
            hypothesis="Baseline scalar loop",
        )

    def seed_candidates(self, ctx: ProblemRunContext) -> list[Candidate]:
        seeds: list[Candidate] = []
        for idx, (unroll, vec_width) in enumerate(((2, 1), (2, 2), (4, 2), (4, 4))):
            if idx >= self.config.seed_count:
                break
            seeds.append(
                self._make_candidate(
                    run_id=ctx.run_id,
                    params={"unroll": unroll, "vec_width": vec_width},
                    operation="seed",
                    agent_id=f"seed-{idx}",
                    hypothesis=f"Test unroll={unroll}, vec_width={vec_width}",
                )
            )
        return seeds

    def static_check(self, candidate: Candidate) -> StaticCheckResult:
        reasons: list[str] = []
        params = candidate.representation.params

        if candidate.representation.language not in {"cuda_cpp", "ptx"}:
            reasons.append("language must be cuda_cpp or ptx")

        unroll = int(params.get("unroll", 0))
        vec_width = int(params.get("vec_width", 0))

        if unroll <= 0:
            reasons.append("unroll must be > 0")
        if vec_width <= 0:
            reasons.append("vec_width must be > 0")
        if vec_width & (vec_width - 1) != 0:
            reasons.append("vec_width must be a power of two")
        if unroll > 16:
            reasons.append("unroll too large for v1 policy")
        if vec_width > 8:
            reasons.append("vec_width too large for v1 policy")

        if self.config.backend not in {"python-sim", "nvcc"}:
            reasons.append(f"unsupported backend: {self.config.backend}")

        return StaticCheckResult(candidate_id=candidate.candidate_id, ok=not reasons, reasons=reasons)

    def build(self, candidate: Candidate) -> BuildExecution:
        if self.config.backend == "nvcc":
            return self._build_nvcc(candidate)
        return self._build_python_sim(candidate)

    def validate(self, candidate: Candidate, build: BuildExecution) -> ValidationResult:
        tolerance = ValidationTolerance(
            mode="rtol_atol",
            rtol=self.config.tolerance_rtol,
            atol=self.config.tolerance_atol,
        )

        if build.result.status is not BuildStatus.SUCCESS or build.runtime is None:
            return ValidationResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.ERROR,
                tests_total=0,
                tests_passed=0,
                tolerance=tolerance,
                failing_cases=[ValidationFailureCase(case_id="build", summary="build failed")],
            )

        runtime = build.runtime
        rng = random.Random(7)
        sizes = [1, 16, 127, self.config.validation_size]
        use_float32_reference = self.config.backend == "nvcc"
        tests_passed = 0
        max_abs_error = 0.0
        max_rel_error = 0.0
        failures: list[ValidationFailureCase] = []

        for size in sizes:
            a = [rng.uniform(-10.0, 10.0) for _ in range(size)]
            b = [rng.uniform(-10.0, 10.0) for _ in range(size)]

            if use_float32_reference:
                expected = [self._f32_add(x, y) for x, y in zip(a, b)]
            else:
                expected = [x + y for x, y in zip(a, b)]
            try:
                got = runtime.run(a, b)
            except Exception as exc:
                failures.append(ValidationFailureCase(case_id=f"size={size}", summary=f"runtime error: {exc}"))
                continue

            case_failed = False
            for i, (exp, act) in enumerate(zip(expected, got)):
                abs_err = abs(exp - act)
                rel_err = abs_err / max(abs(exp), 1e-12)
                max_abs_error = max(max_abs_error, abs_err)
                max_rel_error = max(max_rel_error, rel_err)
                limit = tolerance.atol + (tolerance.rtol * abs(exp))
                if abs_err > limit:
                    failures.append(
                        ValidationFailureCase(
                            case_id=f"size={size}",
                            summary=(
                                f"mismatch at idx={i}: expected={exp:.8f}, "
                                f"actual={act:.8f}, abs_err={abs_err:.4e}"
                            ),
                        )
                    )
                    case_failed = True
                    break
            if not case_failed:
                tests_passed += 1

        status = ValidationStatus.PASS if tests_passed == len(sizes) else ValidationStatus.FAIL
        return ValidationResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=status,
            tests_total=len(sizes),
            tests_passed=tests_passed,
            tolerance=tolerance,
            max_abs_error=max_abs_error,
            max_rel_error=max_rel_error,
            failing_cases=failures,
        )

    @staticmethod
    def _f32_add(x: float, y: float) -> float:
        x_f32 = ctypes.c_float(x).value
        y_f32 = ctypes.c_float(y).value
        return float(ctypes.c_float(x_f32 + y_f32).value)

    def benchmark(
        self,
        candidate: Candidate,
        build: BuildExecution,
        stage: BenchmarkStage,
    ) -> BenchmarkResult:
        if build.result.status is not BuildStatus.SUCCESS or build.runtime is None:
            return BenchmarkResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                stage=stage,
                status=BenchmarkStatus.ERROR,
                samples=0,
                warmup_iters=0,
                timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                env={"backend": self.config.backend},
                profile={},
            )

        if stage is BenchmarkStage.QUICK:
            size = self.config.quick_size
            warmup = self.config.quick_warmup
            iters = self.config.quick_iters
        else:
            size = self.config.full_size
            warmup = self.config.full_warmup
            iters = self.config.full_iters

        runtime = build.runtime
        try:
            samples_us = runtime.benchmark(size, warmup, iters)
        except Exception as exc:
            return BenchmarkResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                stage=stage,
                status=BenchmarkStatus.ERROR,
                samples=0,
                warmup_iters=warmup,
                timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                env={"backend": self.config.backend},
                profile={"error": str(exc)},
            )

        median_us, p95_us, mean_us, stdev_us, cov = summarize(samples_us)
        return BenchmarkResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            stage=stage,
            status=BenchmarkStatus.SUCCESS,
            samples=len(samples_us),
            warmup_iters=warmup,
            timing=BenchmarkTiming(
                median_us=median_us,
                p95_us=p95_us,
                mean_us=mean_us,
                stdev_us=stdev_us,
                cov=cov,
            ),
            env={
                "backend": self.config.backend,
                "python": sys.version.split()[0],
                "size": size,
                "unroll": int(candidate.representation.params.get("unroll", 0)),
                "vec_width": int(candidate.representation.params.get("vec_width", 0)),
            },
            profile={"nsight_compute": {"enabled": False, "report_path": None}},
        )

    def score(self, benchmark: BenchmarkResult, validation: ValidationResult) -> float | dict[str, float]:
        if validation.status is not ValidationStatus.PASS:
            return {"fitness": -1e18, "valid": 0.0}
        if benchmark.status is not BenchmarkStatus.SUCCESS:
            return {"fitness": -1e18, "valid": 1.0}

        latency = max(benchmark.timing.median_us, 1e-9)
        fitness = 1_000_000.0 / latency
        return {"fitness": fitness, "median_us": benchmark.timing.median_us, "valid": 1.0}

    def describe(
        self,
        candidate: Candidate,
        build: BuildExecution,
        benchmark: BenchmarkResult,
    ) -> Descriptor:
        regs = int(build.result.compiler_metrics.get("registers_per_thread", 0))
        smem = int(build.result.compiler_metrics.get("smem_static_bytes", 0)) + int(
            build.result.compiler_metrics.get("smem_dynamic_bytes", 0)
        )
        occupancy = float(build.result.compiler_metrics.get("occupancy_estimate", 0.0))

        reg_bin = 0 if regs < 32 else 1 if regs < 48 else 2 if regs < 64 else 3
        smem_bin = 0 if smem == 0 else 1 if smem < 16_384 else 2 if smem < 48_000 else 3
        occ_bin = 0 if occupancy < 0.25 else 1 if occupancy < 0.5 else 2 if occupancy < 0.75 else 3
        block_x = int(candidate.representation.launch.block[0]) if candidate.representation.launch.block else 256
        launch_block_bin = max(0, min(7, int((max(32, min(1024, block_x)) - 32) / 128)))
        source = candidate.representation.files[0].content if candidate.representation.files else ""
        source_ops_score = sum(source.count(token) for token in ("for", "if", "__global__", "threadIdx", "blockIdx"))
        source_ops_bin = max(0, min(7, source_ops_score))

        return Descriptor(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            descriptor_name="default_v1",
            values={
                "reg_pressure_bin": reg_bin,
                "smem_bin": smem_bin,
                "occupancy_bin": occ_bin,
                "launch_block_bin": launch_block_bin,
                "source_ops_bin": source_ops_bin,
                "unroll": int(candidate.representation.params.get("unroll", 0)),
                "vec_width": int(candidate.representation.params.get("vec_width", 0)),
                "stage_full": 1 if benchmark.stage is BenchmarkStage.FULL else 0,
            },
        )

    def _build_python_sim(self, candidate: Candidate) -> BuildExecution:
        start_ns = time.perf_counter_ns()
        static = self.static_check(candidate)
        if not static.ok:
            stderr = "; ".join(static.reasons)
            result = BuildResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=BuildStatus.FAILURE,
                build_backend="python-sim",
                duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                stderr_digest=sha256_text(stderr),
                artifacts={},
                compiler_metrics={},
                toolchain_fingerprint={"backend": "python-sim", "python": sys.version.split()[0]},
            )
            return BuildExecution(result=result, runtime=None)

        params = candidate.representation.params
        unroll = int(params["unroll"])
        vec_width = int(params["vec_width"])
        runtime = _PythonSimRuntime(unroll=unroll, vec_width=vec_width)

        registers = 28 + (4 * unroll) + (3 * vec_width)
        occupancy_est = max(0.2, min(1.0, (128.0 - registers) / 128.0))

        result = BuildResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=BuildStatus.SUCCESS,
            build_backend="python-sim",
            duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
            stderr_digest=sha256_text(""),
            artifacts={"module": "in-memory"},
            compiler_metrics={
                "registers_per_thread": registers,
                "smem_static_bytes": 0,
                "smem_dynamic_bytes": 0,
                "spill_stores": 0,
                "spill_loads": 0,
                "occupancy_estimate": occupancy_est,
            },
            toolchain_fingerprint={"backend": "python-sim", "python": sys.version.split()[0]},
        )
        return BuildExecution(result=result, runtime=runtime)

    def _build_nvcc(self, candidate: Candidate) -> BuildExecution:
        start_ns = time.perf_counter_ns()
        static = self.static_check(candidate)
        if not static.ok:
            stderr = "; ".join(static.reasons)
            result = BuildResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=BuildStatus.FAILURE,
                build_backend="nvcc-shared-lib",
                duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                stderr_digest=sha256_text(stderr),
                artifacts={},
                compiler_metrics={},
                toolchain_fingerprint={"backend": "nvcc-shared-lib"},
            )
            return BuildExecution(result=result, runtime=None)

        nvcc = shutil.which("nvcc")
        if not nvcc:
            stderr = "nvcc not found"
            result = BuildResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=BuildStatus.INFRA_ERROR,
                build_backend="nvcc-shared-lib",
                duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                stderr_digest=sha256_text(stderr),
                artifacts={},
                compiler_metrics={},
                toolchain_fingerprint={"backend": "nvcc-shared-lib"},
            )
            return BuildExecution(result=result, runtime=None)

        build_dir = Path(tempfile.mkdtemp(prefix="kernelswarm_nvcc_"))
        src_path = build_dir / "vector_add.cu"
        so_path = build_dir / "libvector_add.so"
        src_path.write_text(candidate.representation.files[0].content, encoding="utf-8")

        requested_arch = (candidate.representation.compile.arch or "").strip()
        arch = self._resolve_nvcc_arch(requested_arch)
        flags = list(candidate.representation.compile.flags)
        cmd = [
            nvcc,
            "-shared",
            "-Xcompiler",
            "-fPIC",
            "-Xptxas",
            "-v",
            f"-arch={arch}",
            "-o",
            str(so_path),
            str(src_path),
        ]
        cmd.extend(flags)

        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        stderr = (completed.stderr or "") + "\n" + (completed.stdout or "")

        toolchain = {
            "backend": "nvcc-shared-lib",
            "nvcc_path": nvcc,
            "requested_arch": requested_arch or "auto",
            "arch": arch,
        }
        nvcc_version = self._capture_cmd([nvcc, "--version"])
        if nvcc_version:
            toolchain["nvcc_version"] = nvcc_version

        compiler_metrics = self._parse_ptxas_metrics(stderr)
        if "registers_per_thread" in compiler_metrics:
            regs = int(compiler_metrics["registers_per_thread"])
            compiler_metrics["occupancy_estimate"] = max(0.2, min(1.0, (128.0 - regs) / 128.0))

        if completed.returncode != 0 or not so_path.exists():
            result = BuildResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=BuildStatus.FAILURE,
                build_backend="nvcc-shared-lib",
                duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                stderr_digest=sha256_text(stderr),
                artifacts={"build_dir": str(build_dir), "source": str(src_path)},
                compiler_metrics=compiler_metrics,
                toolchain_fingerprint=toolchain,
            )
            return BuildExecution(result=result, runtime=None)

        block_size = int(candidate.representation.launch.block[0]) if candidate.representation.launch.block else self.config.default_block_size
        try:
            runtime = _NvccRuntime(so_path, block_size=block_size)
        except Exception as exc:
            result = BuildResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=BuildStatus.INFRA_ERROR,
                build_backend="nvcc-shared-lib",
                duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                stderr_digest=sha256_text(f"{stderr}\n{exc}"),
                artifacts={"build_dir": str(build_dir), "source": str(src_path), "library": str(so_path)},
                compiler_metrics=compiler_metrics,
                toolchain_fingerprint=toolchain,
            )
            return BuildExecution(result=result, runtime=None)

        result = BuildResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=BuildStatus.SUCCESS,
            build_backend="nvcc-shared-lib",
            duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
            stderr_digest=sha256_text(stderr),
            artifacts={"build_dir": str(build_dir), "source": str(src_path), "library": str(so_path)},
            compiler_metrics=compiler_metrics,
            toolchain_fingerprint=toolchain,
        )
        return BuildExecution(result=result, runtime=runtime)

    def _resolve_nvcc_arch(self, requested_arch: str) -> str:
        normalized = requested_arch.strip().lower()
        if normalized and normalized not in {"auto", "native"}:
            return requested_arch

        detected = self._detect_gpu_arch()
        if detected:
            return detected

        # Conservative fallback that works on common cloud GPUs and CUDA 11.x toolchains.
        return "sm_75"

    @classmethod
    def _detect_gpu_arch(cls) -> str | None:
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return None

        output = cls._capture_cmd([nvidia_smi, "--query-gpu=compute_cap", "--format=csv,noheader"])
        if not output:
            return None

        first_line = output.splitlines()[0].strip()
        match = re.search(r"(\d+)\.(\d+)", first_line)
        if match:
            major = match.group(1)
            minor = match.group(2)
            return f"sm_{major}{minor}"

        return None

    @staticmethod
    def _capture_cmd(cmd: list[str]) -> str | None:
        try:
            completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except OSError:
            return None
        if completed.returncode != 0:
            return None
        output = completed.stdout.strip() or completed.stderr.strip()
        return output.splitlines()[0] if output else None

    @staticmethod
    def _parse_ptxas_metrics(stderr: str) -> dict[str, int | float]:
        # Minimal parser for lines like:
        # ptxas info    : Used 16 registers, 0 bytes smem, 8 bytes cmem[0]
        metrics: dict[str, int | float] = {}
        for line in stderr.splitlines():
            line = line.strip()
            if "Used" not in line or "registers" not in line:
                continue
            parts = line.replace(",", "").split()
            for idx, token in enumerate(parts):
                if token == "Used" and idx + 1 < len(parts):
                    try:
                        metrics["registers_per_thread"] = int(parts[idx + 1])
                    except ValueError:
                        pass
                if token == "smem" and idx - 1 >= 0:
                    try:
                        metrics["smem_static_bytes"] = int(parts[idx - 1])
                    except ValueError:
                        pass
        metrics.setdefault("smem_dynamic_bytes", 0)
        metrics.setdefault("spill_stores", 0)
        metrics.setdefault("spill_loads", 0)
        return metrics

    def _make_candidate(
        self,
        *,
        run_id: str,
        params: dict[str, int],
        operation: str,
        agent_id: str,
        hypothesis: str,
    ) -> Candidate:
        code = self._code_template(unroll=params["unroll"], vec_width=params["vec_width"])
        representation = CandidateRepresentation(
            language="cuda_cpp",
            entrypoints=["vector_add_kernel"],
            files=[SourceFile(path="vector_add.cu", content=code)],
            params=dict(params),
            launch=LaunchConfig(grid=("auto", 1, 1), block=(self.config.default_block_size, 1, 1), dynamic_smem_bytes=0),
            compile=CompileConfig(arch=self.config.default_arch, flags=["-O3"], defines={"USE_FAST_MATH": "1"}),
        )
        return Candidate.new(
            run_id=run_id,
            parent_ids=[],
            origin=CandidateOrigin(island_id="island-a", agent_id=agent_id, operation=operation),
            representation=representation,
            track="from_scratch",
            hypothesis=hypothesis,
        )

    @staticmethod
    def _code_template(*, unroll: int, vec_width: int) -> str:
        return f"""
#include <cuda_runtime.h>
#include <stdlib.h>

#ifndef UNROLL
#define UNROLL {unroll}
#endif

#ifndef VEC_WIDTH
#define VEC_WIDTH {vec_width}
#endif

__global__ void vector_add_kernel(const float* a, const float* b, float* c, int n) {{
    int t = blockIdx.x * blockDim.x + threadIdx.x;
    int base = t * UNROLL * VEC_WIDTH;
    int stride = blockDim.x * gridDim.x * UNROLL * VEC_WIDTH;

    for (int idx_base = base; idx_base < n; idx_base += stride) {{
#pragma unroll
        for (int u = 0; u < UNROLL; ++u) {{
            int offset = idx_base + (u * VEC_WIDTH);
#pragma unroll
            for (int v = 0; v < VEC_WIDTH; ++v) {{
                int idx = offset + v;
                if (idx < n) {{
                    c[idx] = a[idx] + b[idx];
                }}
            }}
        }}
    }}
}}

extern "C" int vector_add_run(const float* h_a, const float* h_b, float* h_c, int n, int block_size) {{
    cudaError_t status = cudaSuccess;
    float* d_a = NULL;
    float* d_b = NULL;
    float* d_c = NULL;
    int grid = (n + block_size - 1) / block_size;
    if (grid < 1) grid = 1;

    status = cudaMalloc((void**)&d_a, n * sizeof(float));
    if (status != cudaSuccess) goto cleanup;
    status = cudaMalloc((void**)&d_b, n * sizeof(float));
    if (status != cudaSuccess) goto cleanup;
    status = cudaMalloc((void**)&d_c, n * sizeof(float));
    if (status != cudaSuccess) goto cleanup;

    status = cudaMemcpy(d_a, h_a, n * sizeof(float), cudaMemcpyHostToDevice);
    if (status != cudaSuccess) goto cleanup;
    status = cudaMemcpy(d_b, h_b, n * sizeof(float), cudaMemcpyHostToDevice);
    if (status != cudaSuccess) goto cleanup;

    vector_add_kernel<<<grid, block_size>>>(d_a, d_b, d_c, n);
    status = cudaGetLastError();
    if (status != cudaSuccess) goto cleanup;
    status = cudaDeviceSynchronize();
    if (status != cudaSuccess) goto cleanup;

    status = cudaMemcpy(h_c, d_c, n * sizeof(float), cudaMemcpyDeviceToHost);

cleanup:
    if (d_a) cudaFree(d_a);
    if (d_b) cudaFree(d_b);
    if (d_c) cudaFree(d_c);
    return (int)status;
}}

extern "C" int vector_add_benchmark(int n, int block_size, int warmup, int iters, float* out_times_us) {{
    cudaError_t status = cudaSuccess;
    float* h_a = NULL;
    float* h_b = NULL;
    float* d_a = NULL;
    float* d_b = NULL;
    float* d_c = NULL;
    cudaEvent_t start = NULL;
    cudaEvent_t stop = NULL;
    int grid = (n + block_size - 1) / block_size;
    if (grid < 1) grid = 1;

    h_a = (float*)malloc(n * sizeof(float));
    h_b = (float*)malloc(n * sizeof(float));
    if (!h_a || !h_b) {{
        status = cudaErrorMemoryAllocation;
        goto cleanup;
    }}
    for (int i = 0; i < n; ++i) {{
        h_a[i] = (float)((i % 97) * 0.01f);
        h_b[i] = (float)((i % 89) * 0.02f);
    }}

    status = cudaMalloc((void**)&d_a, n * sizeof(float));
    if (status != cudaSuccess) goto cleanup;
    status = cudaMalloc((void**)&d_b, n * sizeof(float));
    if (status != cudaSuccess) goto cleanup;
    status = cudaMalloc((void**)&d_c, n * sizeof(float));
    if (status != cudaSuccess) goto cleanup;

    status = cudaMemcpy(d_a, h_a, n * sizeof(float), cudaMemcpyHostToDevice);
    if (status != cudaSuccess) goto cleanup;
    status = cudaMemcpy(d_b, h_b, n * sizeof(float), cudaMemcpyHostToDevice);
    if (status != cudaSuccess) goto cleanup;

    status = cudaEventCreate(&start);
    if (status != cudaSuccess) goto cleanup;
    status = cudaEventCreate(&stop);
    if (status != cudaSuccess) goto cleanup;

    for (int i = 0; i < warmup; ++i) {{
        vector_add_kernel<<<grid, block_size>>>(d_a, d_b, d_c, n);
    }}
    status = cudaDeviceSynchronize();
    if (status != cudaSuccess) goto cleanup;

    for (int i = 0; i < iters; ++i) {{
        status = cudaEventRecord(start, 0);
        if (status != cudaSuccess) goto cleanup;

        vector_add_kernel<<<grid, block_size>>>(d_a, d_b, d_c, n);
        status = cudaGetLastError();
        if (status != cudaSuccess) goto cleanup;

        status = cudaEventRecord(stop, 0);
        if (status != cudaSuccess) goto cleanup;
        status = cudaEventSynchronize(stop);
        if (status != cudaSuccess) goto cleanup;

        float elapsed_ms = 0.0f;
        status = cudaEventElapsedTime(&elapsed_ms, start, stop);
        if (status != cudaSuccess) goto cleanup;
        out_times_us[i] = elapsed_ms * 1000.0f;
    }}

cleanup:
    if (start) cudaEventDestroy(start);
    if (stop) cudaEventDestroy(stop);
    if (d_a) cudaFree(d_a);
    if (d_b) cudaFree(d_b);
    if (d_c) cudaFree(d_c);
    if (h_a) free(h_a);
    if (h_b) free(h_b);
    return (int)status;
}}
""".strip() + "\n"
