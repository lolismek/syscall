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
class _StencilRuntime:
    tile_x: int
    tile_y: int
    unroll_y: int

    def run(self, grid: list[float], width: int, height: int) -> list[float]:
        out = [0.0] * (width * height)
        y_step = max(1, self.unroll_y)
        for y0 in range(1, height - 1, y_step):
            y_limit = min(height - 1, y0 + y_step)
            for y in range(y0, y_limit):
                row = y * width
                for x in range(1, width - 1):
                    idx = row + x
                    out[idx] = (
                        grid[idx]
                        + grid[idx - 1]
                        + grid[idx + 1]
                        + grid[idx - width]
                        + grid[idx + width]
                    ) / 5.0
        return out

    def benchmark(self, *, side: int, warmup: int, iters: int) -> list[float]:
        n = side * side
        rng = random.Random(41)
        grid = [rng.uniform(-1.0, 1.0) for _ in range(n)]
        for _ in range(warmup):
            self.run(grid, side, side)

        samples_us: list[float] = []
        for _ in range(iters):
            start = time.perf_counter_ns()
            self.run(grid, side, side)
            end = time.perf_counter_ns()
            samples_us.append((end - start) / 1_000.0)
        return samples_us


@dataclass(slots=True)
class Stencil2DConfig:
    backend: str = "python-sim"
    default_arch: str = "auto"
    quick_size: int = 96
    full_size: int = 192
    quick_warmup: int = 2
    quick_iters: int = 10
    full_warmup: int = 3
    full_iters: int = 20
    validation_size: int = 64
    seed_count: int = 4
    tolerance_rtol: float = 1e-6
    tolerance_atol: float = 1e-6
    default_block_size: int = 256

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Stencil2DConfig":
        if not data:
            return Stencil2DConfig()
        allowed = {field.name for field in Stencil2DConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in allowed}
        return Stencil2DConfig(**filtered)


class Stencil2DProblem(OptimizationProblem):
    def __init__(self, config: Stencil2DConfig | None = None) -> None:
        self.config = config or Stencil2DConfig()

    @classmethod
    def from_config_dict(cls, data: dict[str, Any] | None) -> "Stencil2DProblem":
        return cls(Stencil2DConfig.from_dict(data))

    def to_config_dict(self) -> dict[str, Any]:
        return self.config.to_dict()

    def problem_id(self) -> str:
        return "stencil2d_v1"

    def baseline(self, ctx: ProblemRunContext) -> Candidate | None:
        return self._make_candidate(
            run_id=ctx.run_id,
            params={"tile_x": 8, "tile_y": 8, "unroll_y": 1},
            operation="seed",
            agent_id="baseline",
            hypothesis="baseline 5-point stencil",
        )

    def seed_candidates(self, ctx: ProblemRunContext) -> list[Candidate]:
        seeds: list[Candidate] = []
        options = ((8, 8, 2), (16, 8, 1), (16, 16, 2), (32, 8, 2))
        for idx, (tile_x, tile_y, unroll_y) in enumerate(options):
            if idx >= self.config.seed_count:
                break
            seeds.append(
                self._make_candidate(
                    run_id=ctx.run_id,
                    params={"tile_x": tile_x, "tile_y": tile_y, "unroll_y": unroll_y},
                    operation="seed",
                    agent_id=f"seed-{idx}",
                    hypothesis=f"stencil seed tx={tile_x} ty={tile_y} uy={unroll_y}",
                )
            )
        return seeds

    def static_check(self, candidate: Candidate) -> StaticCheckResult:
        params = candidate.representation.params
        reasons: list[str] = []
        if candidate.representation.language not in {"cuda_cpp", "ptx"}:
            reasons.append("language must be cuda_cpp or ptx")
        if self.config.backend != "python-sim":
            reasons.append(f"unsupported backend for stencil2d_v1: {self.config.backend}")

        tile_x = int(params.get("tile_x", 0))
        tile_y = int(params.get("tile_y", 0))
        unroll_y = int(params.get("unroll_y", 0))
        if tile_x not in {8, 16, 32}:
            reasons.append("tile_x must be one of {8,16,32}")
        if tile_y not in {4, 8, 16}:
            reasons.append("tile_y must be one of {4,8,16}")
        if not (1 <= unroll_y <= 4):
            reasons.append("unroll_y must be in [1,4]")
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
                toolchain_fingerprint={"backend": "python-sim", "problem": "stencil2d_v1"},
            )
            return BuildExecution(result=result, runtime=None)

        params = candidate.representation.params
        runtime = _StencilRuntime(
            tile_x=int(params.get("tile_x", 8)),
            tile_y=int(params.get("tile_y", 8)),
            unroll_y=int(params.get("unroll_y", 1)),
        )
        regs = 24 + int(params.get("tile_x", 8)) // 2 + int(params.get("unroll_y", 1)) * 2
        smem = int(params.get("tile_x", 8)) * int(params.get("tile_y", 8)) * 4
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
                "smem_static_bytes": smem,
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
        side = max(8, int(self.config.validation_size))
        rng = random.Random(53)
        grid = [rng.uniform(-1.0, 1.0) for _ in range(side * side)]
        got = runtime.run(grid, side, side)
        expected = self._reference_stencil(grid, side, side)

        max_abs = 0.0
        max_rel = 0.0
        failures: list[ValidationFailureCase] = []
        for idx, (exp, act) in enumerate(zip(expected, got)):
            abs_err = abs(exp - act)
            rel_err = abs_err / max(abs(exp), 1e-12)
            max_abs = max(max_abs, abs_err)
            max_rel = max(max_rel, rel_err)
            limit = tol.atol + (tol.rtol * abs(exp))
            if abs_err > limit:
                y, x = divmod(idx, side)
                failures.append(
                    ValidationFailureCase(
                        case_id=f"y={y},x={x}",
                        summary=f"expected={exp:.8f} got={act:.8f} abs_err={abs_err:.3e}",
                    )
                )
                break

        status = ValidationStatus.PASS if not failures else ValidationStatus.FAIL
        return ValidationResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=status,
            tests_total=1,
            tests_passed=(1 if status is ValidationStatus.PASS else 0),
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
            side, warmup, iters = self.config.quick_size, self.config.quick_warmup, self.config.quick_iters
        else:
            side, warmup, iters = self.config.full_size, self.config.full_warmup, self.config.full_iters
        samples_us = build.runtime.benchmark(side=side, warmup=warmup, iters=iters)
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
            env={"backend": self.config.backend, "side": side, "problem": "stencil2d_v1"},
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
        tile_x = int(params.get("tile_x", 8))
        tile_y = int(params.get("tile_y", 8))
        unroll_y = int(params.get("unroll_y", 1))
        occupancy = float(build.result.compiler_metrics.get("occupancy_estimate", 0.0))
        occ_bin = 0 if occupancy < 0.25 else 1 if occupancy < 0.5 else 2 if occupancy < 0.75 else 3
        return Descriptor(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            descriptor_name="stencil2d_v1",
            values={
                "reg_pressure_bin": 0 if tile_x <= 8 else 1 if tile_x <= 16 else 2 if tile_x <= 24 else 3,
                "occupancy_bin": occ_bin,
                "launch_block_bin": max(0, min(7, int((candidate.representation.launch.block[0] - 32) / 128))),
                "source_ops_bin": min(7, unroll_y + (tile_y // 4)),
                "tile_x": tile_x,
                "tile_y": tile_y,
                "unroll_y": unroll_y,
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
            entrypoints=["stencil_5pt"],
            files=[SourceFile(path="stencil2d.cu", content=self._code_template(params))],
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
    def _reference_stencil(grid: list[float], width: int, height: int) -> list[float]:
        out = [0.0] * (width * height)
        for y in range(1, height - 1):
            row = y * width
            for x in range(1, width - 1):
                idx = row + x
                out[idx] = (
                    grid[idx]
                    + grid[idx - 1]
                    + grid[idx + 1]
                    + grid[idx - width]
                    + grid[idx + width]
                ) / 5.0
        return out

    @staticmethod
    def _code_template(params: dict[str, int]) -> str:
        return f"""
#ifndef TILE_X
#define TILE_X {int(params.get("tile_x", 8))}
#endif
#ifndef TILE_Y
#define TILE_Y {int(params.get("tile_y", 8))}
#endif
#ifndef UNROLL_Y
#define UNROLL_Y {int(params.get("unroll_y", 1))}
#endif
extern "C" __global__ void stencil_5pt(const float* in, float* out, int width, int height) {{
    int x = blockIdx.x * blockDim.x + threadIdx.x;
    int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x <= 0 || x >= (width - 1) || y <= 0 || y >= (height - 1)) {{
        return;
    }}
    int idx = y * width + x;
    out[idx] = (in[idx] + in[idx - 1] + in[idx + 1] + in[idx - width] + in[idx + width]) / 5.0f;
}}
"""
