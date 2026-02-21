from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass

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
class _RuntimeKernel:
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


@dataclass(slots=True)
class VectorAddConfig:
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


class VectorAddProblem(OptimizationProblem):
    def __init__(self, config: VectorAddConfig | None = None) -> None:
        self.config = config or VectorAddConfig()

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

        return StaticCheckResult(candidate_id=candidate.candidate_id, ok=not reasons, reasons=reasons)

    def build(self, candidate: Candidate) -> BuildExecution:
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
        runtime = _RuntimeKernel(unroll=unroll, vec_width=vec_width)

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
        tests_passed = 0
        max_abs_error = 0.0
        max_rel_error = 0.0
        failures: list[ValidationFailureCase] = []

        for size in sizes:
            a = [rng.uniform(-10.0, 10.0) for _ in range(size)]
            b = [rng.uniform(-10.0, 10.0) for _ in range(size)]

            expected = [x + y for x, y in zip(a, b)]
            got = runtime.run(a, b)

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
                env={"backend": "python-sim"},
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

        rng = random.Random(17)
        a = [rng.uniform(-1.0, 1.0) for _ in range(size)]
        b = [rng.uniform(-1.0, 1.0) for _ in range(size)]
        runtime = build.runtime

        for _ in range(warmup):
            runtime.run(a, b)

        samples_us: list[float] = []
        for _ in range(iters):
            start_ns = time.perf_counter_ns()
            runtime.run(a, b)
            end_ns = time.perf_counter_ns()
            samples_us.append((end_ns - start_ns) / 1_000.0)

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
                "backend": "python-sim",
                "python": sys.version.split()[0],
                "size": size,
                "unroll": runtime.unroll,
                "vec_width": runtime.vec_width,
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

        return Descriptor(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            descriptor_name="default_v1",
            values={
                "reg_pressure_bin": reg_bin,
                "smem_bin": smem_bin,
                "occupancy_bin": occ_bin,
                "unroll": int(candidate.representation.params.get("unroll", 0)),
                "vec_width": int(candidate.representation.params.get("vec_width", 0)),
                "stage_full": 1 if benchmark.stage is BenchmarkStage.FULL else 0,
            },
        )

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
            files=[SourceFile(path="kernel.cu", content=code)],
            params=dict(params),
            launch=LaunchConfig(grid=("auto", 1, 1), block=(256, 1, 1), dynamic_smem_bytes=0),
            compile=CompileConfig(arch="sm_90", flags=["-O3"], defines={"USE_FAST_MATH": "1"}),
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
        return (
            "// vector_add kernel candidate (simulated for v1 harness)\n"
            f"// unroll={unroll}, vec_width={vec_width}\n"
            "extern \"C\" __global__ void vector_add_kernel(const float* a, const float* b, float* c, int n) {\n"
            "  int idx = blockIdx.x * blockDim.x + threadIdx.x;\n"
            "  if (idx < n) { c[idx] = a[idx] + b[idx]; }\n"
            "}\n"
        )
