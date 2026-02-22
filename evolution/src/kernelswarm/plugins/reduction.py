from __future__ import annotations

import random
import sys
import time
from dataclasses import asdict, dataclass
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
class _ReductionRuntime:
    unroll: int
    vec_width: int
    tree_arity: int

    def run(self, values: list[float]) -> float:
        if not values:
            return 0.0

        step = max(1, self.unroll * self.vec_width)
        partial: list[float] = []
        for start in range(0, len(values), step):
            stop = min(len(values), start + step)
            subtotal = 0.0
            for idx in range(start, stop):
                subtotal += values[idx]
            partial.append(subtotal)

        arity = max(2, self.tree_arity)
        while len(partial) > 1:
            merged: list[float] = []
            for start in range(0, len(partial), arity):
                subtotal = 0.0
                for value in partial[start : start + arity]:
                    subtotal += value
                merged.append(subtotal)
            partial = merged
        return float(partial[0])

    def benchmark(self, *, n: int, warmup: int, iters: int) -> list[float]:
        rng = random.Random(23)
        values = [rng.uniform(-2.0, 2.0) for _ in range(n)]
        for _ in range(warmup):
            self.run(values)

        samples_us: list[float] = []
        for _ in range(iters):
            start = time.perf_counter_ns()
            self.run(values)
            end = time.perf_counter_ns()
            samples_us.append((end - start) / 1_000.0)
        return samples_us


@dataclass(slots=True)
class ReductionConfig:
    backend: str = "python-sim"
    default_arch: str = "auto"
    quick_size: int = 50_000
    full_size: int = 200_000
    quick_warmup: int = 2
    quick_iters: int = 12
    full_warmup: int = 4
    full_iters: int = 24
    validation_size: int = 32_768
    seed_count: int = 4
    tolerance_rtol: float = 1e-6
    tolerance_atol: float = 1e-6
    default_block_size: int = 256

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "ReductionConfig":
        if not data:
            return ReductionConfig()
        allowed = {field.name for field in ReductionConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in allowed}
        return ReductionConfig(**filtered)


class ReductionProblem(OptimizationProblem):
    def __init__(self, config: ReductionConfig | None = None) -> None:
        self.config = config or ReductionConfig()

    @classmethod
    def from_config_dict(cls, data: dict[str, Any] | None) -> "ReductionProblem":
        return cls(ReductionConfig.from_dict(data))

    def to_config_dict(self) -> dict[str, Any]:
        return self.config.to_dict()

    def problem_id(self) -> str:
        return "reduction_v1"

    def baseline(self, ctx: ProblemRunContext) -> Candidate | None:
        return self._make_candidate(
            run_id=ctx.run_id,
            params={"unroll": 1, "vec_width": 1, "tree_arity": 2},
            operation="seed",
            agent_id="baseline",
            hypothesis="baseline tree reduction",
        )

    def seed_candidates(self, ctx: ProblemRunContext) -> list[Candidate]:
        seeds: list[Candidate] = []
        options = ((2, 1, 2), (2, 2, 4), (4, 2, 4), (4, 4, 8))
        for idx, (unroll, vec_width, tree_arity) in enumerate(options):
            if idx >= self.config.seed_count:
                break
            seeds.append(
                self._make_candidate(
                    run_id=ctx.run_id,
                    params={"unroll": unroll, "vec_width": vec_width, "tree_arity": tree_arity},
                    operation="seed",
                    agent_id=f"seed-{idx}",
                    hypothesis=f"reduction seed u={unroll} vw={vec_width} arity={tree_arity}",
                )
            )
        return seeds

    def static_check(self, candidate: Candidate) -> StaticCheckResult:
        params = candidate.representation.params
        reasons: list[str] = []
        if candidate.representation.language not in {"cuda_cpp", "ptx"}:
            reasons.append("language must be cuda_cpp or ptx")
        if self.config.backend != "python-sim":
            reasons.append(f"unsupported backend for reduction_v1: {self.config.backend}")

        unroll = int(params.get("unroll", 0))
        vec_width = int(params.get("vec_width", 0))
        tree_arity = int(params.get("tree_arity", 0))
        if not (1 <= unroll <= 16):
            reasons.append("unroll must be in [1,16]")
        if vec_width not in {1, 2, 4, 8}:
            reasons.append("vec_width must be one of {1,2,4,8}")
        if tree_arity not in {2, 4, 8}:
            reasons.append("tree_arity must be one of {2,4,8}")
        return StaticCheckResult(candidate_id=candidate.candidate_id, ok=not reasons, reasons=reasons)

    def build(self, candidate: Candidate) -> BuildExecution:
        start = time.perf_counter_ns()
        static = self.static_check(candidate)
        if not static.ok:
            result = BuildResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=BuildStatus.FAILURE,
                build_backend="python-sim",
                duration_ms=int((time.perf_counter_ns() - start) / 1_000_000),
                stderr_digest=sha256_text("; ".join(static.reasons)),
                artifacts={},
                compiler_metrics={},
                toolchain_fingerprint={"backend": "python-sim", "problem": "reduction_v1"},
            )
            return BuildExecution(result=result, runtime=None)

        params = candidate.representation.params
        unroll = int(params.get("unroll", 1))
        vec_width = int(params.get("vec_width", 1))
        tree_arity = int(params.get("tree_arity", 2))
        runtime = _ReductionRuntime(unroll=unroll, vec_width=vec_width, tree_arity=tree_arity)

        regs = 20 + (4 * unroll) + (2 * vec_width) + tree_arity
        occupancy = max(0.2, min(1.0, (128.0 - regs) / 128.0))
        result = BuildResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=BuildStatus.SUCCESS,
            build_backend="python-sim",
            duration_ms=int((time.perf_counter_ns() - start) / 1_000_000),
            stderr_digest=sha256_text(""),
            artifacts={"module": "in-memory"},
            compiler_metrics={
                "registers_per_thread": regs,
                "smem_static_bytes": 0,
                "smem_dynamic_bytes": 0,
                "spill_stores": 0,
                "spill_loads": 0,
                "occupancy_estimate": occupancy,
            },
            toolchain_fingerprint={"backend": "python-sim", "python": sys.version.split()[0]},
        )
        return BuildExecution(result=result, runtime=runtime)

    def validate(self, candidate: Candidate, build: BuildExecution) -> ValidationResult:
        tol = ValidationTolerance(mode="rtol_atol", rtol=self.config.tolerance_rtol, atol=self.config.tolerance_atol)
        if build.result.status is not BuildStatus.SUCCESS or build.runtime is None:
            return ValidationResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.ERROR,
                tests_total=0,
                tests_passed=0,
                tolerance=tol,
                failing_cases=[ValidationFailureCase(case_id="build", summary="build failed")],
            )

        runtime = build.runtime
        rng = random.Random(31)
        tests = [1, 17, 1024, self.config.validation_size]
        failures: list[ValidationFailureCase] = []
        max_abs = 0.0
        max_rel = 0.0
        passed = 0
        for n in tests:
            values = [rng.uniform(-4.0, 4.0) for _ in range(n)]
            expected = sum(values)
            got = runtime.run(values)
            abs_err = abs(expected - got)
            rel_err = abs_err / max(abs(expected), 1e-12)
            max_abs = max(max_abs, abs_err)
            max_rel = max(max_rel, rel_err)
            limit = tol.atol + (tol.rtol * abs(expected))
            if abs_err > limit:
                failures.append(
                    ValidationFailureCase(
                        case_id=f"n={n}",
                        summary=f"expected={expected:.8f} got={got:.8f} abs_err={abs_err:.3e}",
                    )
                )
            else:
                passed += 1

        status = ValidationStatus.PASS if passed == len(tests) else ValidationStatus.FAIL
        return ValidationResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=status,
            tests_total=len(tests),
            tests_passed=passed,
            tolerance=tol,
            max_abs_error=max_abs,
            max_rel_error=max_rel,
            failing_cases=failures,
        )

    def benchmark(self, candidate: Candidate, build: BuildExecution, stage: BenchmarkStage) -> BenchmarkResult:
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
            n, warmup, iters = self.config.quick_size, self.config.quick_warmup, self.config.quick_iters
        else:
            n, warmup, iters = self.config.full_size, self.config.full_warmup, self.config.full_iters

        samples_us = build.runtime.benchmark(n=n, warmup=warmup, iters=iters)
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
            env={"backend": self.config.backend, "size": n, "problem": "reduction_v1"},
            profile={},
        )

    def score(self, benchmark: BenchmarkResult, validation: ValidationResult) -> float | dict[str, float]:
        if validation.status is not ValidationStatus.PASS:
            return {"fitness": -1e18, "valid": 0.0}
        if benchmark.status is not BenchmarkStatus.SUCCESS:
            return {"fitness": -1e18, "valid": 1.0}
        latency = max(benchmark.timing.median_us, 1e-9)
        fitness = 1_000_000.0 / latency
        return {"fitness": fitness, "median_us": benchmark.timing.median_us, "valid": 1.0}

    def describe(self, candidate: Candidate, build: BuildExecution, benchmark: BenchmarkResult) -> Descriptor:
        params = candidate.representation.params
        unroll = int(params.get("unroll", 1))
        vec_width = int(params.get("vec_width", 1))
        arity = int(params.get("tree_arity", 2))
        occupancy = float(build.result.compiler_metrics.get("occupancy_estimate", 0.0))
        occ_bin = 0 if occupancy < 0.25 else 1 if occupancy < 0.5 else 2 if occupancy < 0.75 else 3
        return Descriptor(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            descriptor_name="reduction_v1",
            values={
                "reg_pressure_bin": 0 if unroll <= 2 else 1 if unroll <= 4 else 2 if unroll <= 8 else 3,
                "occupancy_bin": occ_bin,
                "launch_block_bin": max(0, min(7, int((candidate.representation.launch.block[0] - 32) / 128))),
                "source_ops_bin": min(7, arity),
                "unroll": unroll,
                "vec_width": vec_width,
                "tree_arity": arity,
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
        rep = CandidateRepresentation(
            language="cuda_cpp",
            entrypoints=["reduce_kernel"],
            files=[SourceFile(path="reduction.cu", content=self._code_template(params))],
            params=dict(params),
            launch=LaunchConfig(block=(self.config.default_block_size, 1, 1)),
            compile=CompileConfig(arch=self.config.default_arch, flags=["-O3"], defines={"USE_FAST_MATH": "1"}),
        )
        return Candidate.new(
            run_id=run_id,
            parent_ids=[],
            origin=CandidateOrigin(island_id="island-a", agent_id=agent_id, operation=operation),
            representation=rep,
            track="from_scratch",
            hypothesis=hypothesis,
        )

    @staticmethod
    def _code_template(params: dict[str, int]) -> str:
        return f"""
#ifndef UNROLL
#define UNROLL {int(params.get("unroll", 1))}
#endif
#ifndef VEC_WIDTH
#define VEC_WIDTH {int(params.get("vec_width", 1))}
#endif
#ifndef TREE_ARITY
#define TREE_ARITY {int(params.get("tree_arity", 2))}
#endif
extern "C" __global__ void reduce_kernel(const float* in, float* out, int n) {{
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    float acc = 0.0f;
    int stride = blockDim.x * gridDim.x * UNROLL * VEC_WIDTH;
    for (int base = idx * UNROLL * VEC_WIDTH; base < n; base += stride) {{
#pragma unroll
        for (int u = 0; u < UNROLL; ++u) {{
#pragma unroll
            for (int v = 0; v < VEC_WIDTH; ++v) {{
                int j = base + (u * VEC_WIDTH) + v;
                if (j < n) {{
                    acc += in[j];
                }}
            }}
        }}
    }}
    // tree merge is done in later passes (simulated by runtime model)
    out[idx] = acc;
}}
"""
