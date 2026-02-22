from __future__ import annotations

import importlib
import importlib.metadata
import json
import logging
import re
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

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

_ALLOWED_BACKENDS = {"cuda", "triton", "tilelang", "cute", "thunderkittens", "cutlass"}
_ALLOWED_PRECISIONS = {"fp16", "fp32", "bf16"}
_SAFETY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\btry\s*:", "try/except blocks are not allowed in candidate source"),
    (r"\bexcept\b", "try/except blocks are not allowed in candidate source"),
    (r"threading\\.Thread\s*\(", "threading is not allowed in candidate source"),
    (
        r"torch\\.cuda\\.Event\\.(record|elapsed_time)\s*=",
        "timing monkey patch patterns are not allowed",
    ),
)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


@dataclass(slots=True)
class _KernelBenchEvalResult:
    compiled: bool
    correctness: bool
    runtime_ms: float
    runtime_stats: dict[str, Any]
    ref_runtime_ms: float
    ref_runtime_stats: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(slots=True)
class _KernelBenchRuntime:
    build_dir: Path
    device: Any
    precision_dtype: Any
    ref_source: str
    ref_name: str
    kb_eval_module: Any
    build_eval: _KernelBenchEvalResult
    benchmark_cache: dict[str, _KernelBenchEvalResult] = field(default_factory=dict)


@dataclass(slots=True)
class _KernelBenchStaticRuntime:
    kb_eval_module: Any
    ref_source: str
    ref_name: str
    precision_dtype: Any


@dataclass(slots=True)
class KernelBenchConfig:
    level: int = 1
    problem_id: int = 23
    dataset_source: str = "local"
    dataset_name: str = "ScalingIntelligence/KernelBench"
    dataset_base_path: str | None = None
    repo_path: str | None = None
    backend: str = "cuda"
    precision: str = "fp32"
    device: int = 0
    timing_method: str = "cuda_event"
    seed_count: int = 3
    quick_correct_trials: int = 1
    quick_perf_trials: int = 4
    full_correct_trials: int = 1
    full_perf_trials: int = 20
    build_dir_root: str | None = None
    static_check_enabled: bool = True
    static_fail_on_warning: bool = False
    verbose: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "KernelBenchConfig":
        if not data:
            return KernelBenchConfig()

        allowed = {field.name for field in KernelBenchConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in allowed}

        if "level" in filtered:
            filtered["level"] = int(filtered["level"])
        if "problem_id" in filtered:
            filtered["problem_id"] = int(filtered["problem_id"])
        if "device" in filtered:
            filtered["device"] = int(filtered["device"])

        for key in (
            "seed_count",
            "quick_correct_trials",
            "quick_perf_trials",
            "full_correct_trials",
            "full_perf_trials",
        ):
            if key in filtered:
                filtered[key] = int(filtered[key])

        for key in ("static_check_enabled", "static_fail_on_warning", "verbose"):
            if key in filtered:
                filtered[key] = _to_bool(filtered[key])

        return KernelBenchConfig(**filtered)


class KernelBenchProblem(OptimizationProblem):
    def __init__(self, config: KernelBenchConfig | None = None) -> None:
        self.config = config or KernelBenchConfig()
        self._static_runtime: _KernelBenchStaticRuntime | None = None
        self._static_runtime_lock = threading.Lock()
        self._ref_runtime_cache: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
        self._ref_runtime_lock = threading.Lock()

    @classmethod
    def from_config_dict(cls, data: dict[str, Any] | None) -> "KernelBenchProblem":
        return cls(KernelBenchConfig.from_dict(data))

    def to_config_dict(self) -> dict[str, Any]:
        return self.config.to_dict()

    def problem_id(self) -> str:
        return "kernelbench_v1"

    def generator_prompt_context(self) -> dict[str, Any]:
        """Provide rich context so the LLM knows what to optimize."""
        ref_source = ""
        ref_name = f"level_{self.config.level}/problem_{self.config.problem_id}"

        # Try full runtime first (works on GPU boxes with KernelBench installed).
        try:
            static_rt = self._get_static_runtime()
            ref_source = static_rt.ref_source
            ref_name = static_rt.ref_name
        except Exception:
            pass

        # Fallback: read reference source directly from KernelBench repo on disk.
        if not ref_source:
            ref_source, ref_name = self._load_ref_source_from_disk()

        return {
            "mode": "kernelbench",
            "ref_source": ref_source,
            "ref_name": ref_name,
            "problem_level": self.config.level,
            "problem_id": self.config.problem_id,
            "backend": self.config.backend,
            "precision": self.config.precision,
            "hardware": "NVIDIA L40S (Ada Lovelace, 48GB GDDR6 ECC, 735 GB/s memory bandwidth, 181.05 TFLOPS FP32, 362.05 TFLOPS TF32, compute capability 8.9)",
        }

    def _load_ref_source_from_disk(self) -> tuple[str, str]:
        """Load reference source directly from KernelBench repo files (no imports needed)."""
        # Try configured repo_path first, then common local paths.
        candidates = []
        if self.config.repo_path:
            candidates.append(Path(self.config.repo_path).expanduser())
        candidates.append(Path.home() / "KernelBench")

        for repo_path in candidates:
            level_dir = repo_path / "KernelBench" / f"level{self.config.level}"
            if not level_dir.is_dir():
                continue
            prefix = f"{self.config.problem_id}_"
            for py_file in sorted(level_dir.iterdir()):
                if py_file.name.startswith(prefix) and py_file.suffix == ".py":
                    try:
                        source = py_file.read_text(encoding="utf-8")
                        name = py_file.stem
                        _log.info("Loaded reference source from disk: %s", py_file)
                        return source, name
                    except Exception as exc:
                        _log.warning("Failed to read reference source from %s: %s", py_file, exc)

        return "", f"level_{self.config.level}/problem_{self.config.problem_id}"

    def baseline(self, ctx: ProblemRunContext) -> Candidate | None:
        baseline_src = self._seed_sources()[0]
        return self._make_candidate(
            run_id=ctx.run_id,
            source=baseline_src,
            operation="seed",
            agent_id="baseline",
            hypothesis="KernelBench baseline wrapper around reference model",
        )

    def seed_candidates(self, ctx: ProblemRunContext) -> list[Candidate]:
        out: list[Candidate] = []
        for idx, source in enumerate(self._seed_sources()[1:]):
            if idx >= max(0, self.config.seed_count - 1):
                break
            out.append(
                self._make_candidate(
                    run_id=ctx.run_id,
                    source=source,
                    operation="seed",
                    agent_id=f"kb-seed-{idx}",
                    hypothesis=f"KernelBench seed variant {idx}",
                )
            )
        return out

    def static_check(self, candidate: Candidate) -> StaticCheckResult:
        reasons: list[str] = []
        warnings: list[str] = []
        rep = candidate.representation
        source = self._candidate_source(candidate)

        if rep.language not in {"python", "python_source"}:
            reasons.append("language must be python or python_source")

        if not rep.files:
            reasons.append("candidate must include at least one source file")
        if not source:
            reasons.append("candidate source cannot be empty")
        if "ModelNew" not in source:
            reasons.append("candidate source must define ModelNew")
        if "torch.compile" in source:
            reasons.append("torch.compile is banned — write actual custom kernels")

        if self.config.backend not in _ALLOWED_BACKENDS:
            reasons.append(f"unsupported kernelbench backend: {self.config.backend}")
        if self.config.precision not in _ALLOWED_PRECISIONS:
            reasons.append(f"unsupported precision: {self.config.precision}")
        if self.config.dataset_source not in {"local", "huggingface"}:
            reasons.append("dataset_source must be local or huggingface")

        if self.config.level <= 0:
            reasons.append("level must be >= 1")
        if self.config.problem_id <= 0:
            reasons.append("problem_id must be >= 1")

        # Reject candidates that drop learnable parameters.
        # If the candidate doesn't delegate to Model (torch.compile wrapper etc.)
        # then it must define its own nn.Parameter or use a parameterized module.
        _delegates_to_model = bool(re.search(r'\bModel\s*\(', source))
        _has_parameters = bool(
            re.search(r'nn\.Parameter\b', source)
            or re.search(r'nn\.LayerNorm\b', source)
            or re.search(r'nn\.Linear\b', source)
            or re.search(r'nn\.Conv', source)
            or re.search(r'nn\.BatchNorm', source)
            or re.search(r'nn\.GroupNorm', source)
        )
        if not _delegates_to_model and not _has_parameters:
            reasons.append(
                "candidate must have learnable parameters (nn.Parameter, nn.LayerNorm, etc.) "
                "or delegate to Model — dropping parameters is not allowed"
            )

        if self.config.static_check_enabled:
            for pattern, message in _SAFETY_PATTERNS:
                if re.search(pattern, source):
                    warnings.append(message)
            if self.config.static_fail_on_warning:
                reasons.extend(warnings)

        return StaticCheckResult(
            candidate_id=candidate.candidate_id,
            ok=not reasons,
            reasons=reasons,
        )

    def build(self, candidate: Candidate) -> BuildExecution:
        start_ns = time.perf_counter_ns()
        static = self.static_check(candidate)
        if not static.ok:
            stderr = "; ".join(static.reasons)
            return BuildExecution(
                result=BuildResult(
                    run_id=candidate.run_id,
                    candidate_id=candidate.candidate_id,
                    status=BuildStatus.FAILURE,
                    build_backend="kernelbench",
                    duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                    stderr_digest=sha256_text(stderr),
                    artifacts={},
                    compiler_metrics={},
                    toolchain_fingerprint={"backend": "kernelbench"},
                ),
                runtime=None,
            )

        try:
            static_runtime = self._get_static_runtime()
            build_dir = self._build_dir_for(candidate)
            device = int(self.config.device)

            eval_result = self._evaluate_kernel(
                kb_eval=static_runtime.kb_eval_module,
                ref_source=static_runtime.ref_source,
                candidate_source=self._candidate_source(candidate),
                build_dir=build_dir,
                device=device,
                precision_dtype=static_runtime.precision_dtype,
                measure_performance=True,
                num_correct_trials=max(1, self.config.quick_correct_trials),
                num_perf_trials=max(1, self.config.quick_perf_trials),
            )
        except Exception as exc:
            # Classify candidate-caused GPU/compilation errors as FAILURE (bad code),
            # not INFRA_ERROR.  Only truly unexpected errors are infra.
            exc_name = type(exc).__name__
            _CANDIDATE_ERROR_NAMES = {
                "AcceleratorError", "CompilationError", "CompileError",
                "OutOfMemoryError", "CUDARuntimeError",
            }
            is_candidate_error = (
                exc_name in _CANDIDATE_ERROR_NAMES
                or isinstance(exc, (RuntimeError, SyntaxError, TypeError, ValueError))
            )
            build_status = BuildStatus.FAILURE if is_candidate_error else BuildStatus.INFRA_ERROR
            error_summary = f"{exc_name}: {exc}"
            digest_src = f"{build_status.value}:{error_summary}"
            _log.warning(
                "Kernel eval %s for %s: %s",
                build_status.value,
                candidate.candidate_id[:8],
                error_summary[:200],
            )
            return BuildExecution(
                result=BuildResult(
                    run_id=candidate.run_id,
                    candidate_id=candidate.candidate_id,
                    status=build_status,
                    build_backend="kernelbench",
                    duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
                    stderr_digest=sha256_text(digest_src),
                    artifacts={},
                    compiler_metrics={},
                    toolchain_fingerprint={"backend": "kernelbench", "error_type": exc_name},
                ),
                runtime=None,
            )

        metadata_blob = json.dumps(eval_result.metadata, sort_keys=True, default=str)
        status = BuildStatus.SUCCESS if eval_result.compiled else BuildStatus.FAILURE

        runtime = _KernelBenchRuntime(
            build_dir=build_dir,
            device=device,
            precision_dtype=static_runtime.precision_dtype,
            ref_source=static_runtime.ref_source,
            ref_name=static_runtime.ref_name,
            kb_eval_module=static_runtime.kb_eval_module,
            build_eval=eval_result,
            benchmark_cache={BenchmarkStage.QUICK.value: eval_result},
        )

        toolchain = {
            "backend": "kernelbench",
            "kernelbench_backend": self.config.backend,
            "precision": self.config.precision,
            "timing_method": self.config.timing_method,
            "kernelbench_version": self._kernelbench_version(),
            "problem_level": str(self.config.level),
            "problem_id": str(self.config.problem_id),
        }

        result = BuildResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=status,
            build_backend="kernelbench",
            duration_ms=int((time.perf_counter_ns() - start_ns) / 1_000_000),
            stderr_digest=sha256_text(metadata_blob),
            artifacts={"build_dir": str(build_dir), "ref_problem": static_runtime.ref_name},
            compiler_metrics=self._compiler_metrics_from_eval(eval_result),
            toolchain_fingerprint=toolchain,
        )
        return BuildExecution(result=result, runtime=runtime)

    def validate(self, candidate: Candidate, build: BuildExecution) -> ValidationResult:
        tolerance = ValidationTolerance(mode="kernelbench", rtol=0.0, atol=0.0)
        if build.result.status is not BuildStatus.SUCCESS or not isinstance(build.runtime, _KernelBenchRuntime):
            error_type = (build.result.toolchain_fingerprint or {}).get("error_type", "")
            build_summary = f"build_{build.result.status.value}"
            if error_type:
                build_summary += f": {error_type}"
            return ValidationResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                status=ValidationStatus.ERROR,
                tests_total=0,
                tests_passed=0,
                tolerance=tolerance,
                failing_cases=[ValidationFailureCase(case_id="build", summary=build_summary)],
            )

        eval_result = build.runtime.build_eval
        tests_total = max(1, self.config.quick_correct_trials)
        tests_passed = tests_total if eval_result.correctness else 0

        # Extract numerical error magnitudes from KernelBench metadata.
        max_abs_error = 0.0
        max_diffs = eval_result.metadata.get("max_difference")
        if max_diffs:
            try:
                max_abs_error = float(max_diffs[0] if isinstance(max_diffs, list) else max_diffs)
            except (ValueError, TypeError, IndexError):
                pass

        if eval_result.correctness:
            status = ValidationStatus.PASS
            failures: list[ValidationFailureCase] = []
        else:
            status = ValidationStatus.FAIL
            failures = [
                ValidationFailureCase(
                    case_id="kernelbench_correctness",
                    summary=self._validation_summary(eval_result.metadata),
                )
            ]

        return ValidationResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=status,
            tests_total=tests_total,
            tests_passed=tests_passed,
            tolerance=tolerance,
            max_abs_error=max_abs_error,
            max_rel_error=0.0,
            failing_cases=failures,
        )

    def benchmark(
        self,
        candidate: Candidate,
        build: BuildExecution,
        stage: BenchmarkStage,
    ) -> BenchmarkResult:
        if build.result.status is not BuildStatus.SUCCESS or not isinstance(build.runtime, _KernelBenchRuntime):
            return BenchmarkResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                stage=stage,
                status=BenchmarkStatus.ERROR,
                samples=0,
                warmup_iters=0,
                timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                env={"backend": "kernelbench", "reason": "build failed"},
                profile={},
            )

        runtime = build.runtime
        stage_key = stage.value
        if stage_key in runtime.benchmark_cache:
            eval_result = runtime.benchmark_cache[stage_key]
            if stage is BenchmarkStage.QUICK and not self._has_performance_stats(eval_result):
                runtime.benchmark_cache.pop(stage_key, None)
                return self.benchmark(candidate, build, stage)
        else:
            if stage is BenchmarkStage.QUICK:
                quick_eval = runtime.build_eval
                if self._has_performance_stats(quick_eval):
                    eval_result = quick_eval
                else:
                    try:
                        eval_result = self._evaluate_kernel(
                            kb_eval=runtime.kb_eval_module,
                            ref_source=runtime.ref_source,
                            candidate_source=self._candidate_source(candidate),
                            build_dir=runtime.build_dir,
                            device=runtime.device,
                            precision_dtype=runtime.precision_dtype,
                            measure_performance=True,
                            num_correct_trials=max(1, self.config.quick_correct_trials),
                            num_perf_trials=max(1, self.config.quick_perf_trials),
                        )
                    except Exception as exc:
                        return BenchmarkResult(
                            run_id=candidate.run_id,
                            candidate_id=candidate.candidate_id,
                            stage=stage,
                            status=BenchmarkStatus.ERROR,
                            samples=0,
                            warmup_iters=0,
                            timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                            env={"backend": "kernelbench", "error": f"{type(exc).__name__}: {exc}"},
                            profile={},
                        )
            else:
                num_correct = max(1, self.config.full_correct_trials)
                num_perf = max(1, self.config.full_perf_trials)
                try:
                    eval_result = self._evaluate_kernel(
                        kb_eval=runtime.kb_eval_module,
                        ref_source=runtime.ref_source,
                        candidate_source=self._candidate_source(candidate),
                        build_dir=runtime.build_dir,
                        device=runtime.device,
                        precision_dtype=runtime.precision_dtype,
                        measure_performance=True,
                        num_correct_trials=num_correct,
                        num_perf_trials=num_perf,
                    )
                except Exception as exc:
                    return BenchmarkResult(
                        run_id=candidate.run_id,
                        candidate_id=candidate.candidate_id,
                        stage=stage,
                        status=BenchmarkStatus.ERROR,
                        samples=0,
                        warmup_iters=0,
                        timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                        env={"backend": "kernelbench", "error": f"{type(exc).__name__}: {exc}"},
                        profile={},
                    )
            runtime.benchmark_cache[stage_key] = eval_result

        if not eval_result.compiled or not eval_result.correctness:
            return BenchmarkResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                stage=stage,
                status=BenchmarkStatus.ERROR,
                samples=0,
                warmup_iters=0,
                timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                env={"backend": "kernelbench", "compiled": eval_result.compiled, "correctness": eval_result.correctness},
                profile={"metadata": dict(eval_result.metadata)},
            )

        mean_ms = self._as_float(eval_result.runtime_stats.get("mean"), default=eval_result.runtime_ms)
        std_ms = self._as_float(eval_result.runtime_stats.get("std"), default=0.0)
        max_ms = self._as_float(eval_result.runtime_stats.get("max"), default=mean_ms)

        if mean_ms <= 0.0:
            return BenchmarkResult(
                run_id=candidate.run_id,
                candidate_id=candidate.candidate_id,
                stage=stage,
                status=BenchmarkStatus.ERROR,
                samples=0,
                warmup_iters=0,
                timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
                env={"backend": "kernelbench", "reason": "missing runtime stats"},
                profile={"metadata": dict(eval_result.metadata)},
            )

        mean_us = mean_ms * 1_000.0
        std_us = max(0.0, std_ms) * 1_000.0
        max_us = max(mean_ms, max_ms) * 1_000.0

        samples = int(eval_result.runtime_stats.get("num_trials", 0) or 0)
        if samples <= 0:
            samples = self.config.quick_perf_trials if stage is BenchmarkStage.QUICK else self.config.full_perf_trials

        ref_runtime_ms, ref_runtime_stats = self._resolve_reference_runtime(
            runtime=runtime,
            eval_result=eval_result,
            stage=stage,
        )
        speedup_vs_ref = (ref_runtime_ms / mean_ms) if ref_runtime_ms > 0.0 and mean_ms > 1e-12 else None

        return BenchmarkResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            stage=stage,
            status=BenchmarkStatus.SUCCESS,
            samples=samples,
            warmup_iters=0,
            timing=BenchmarkTiming(
                median_us=mean_us,
                p95_us=max_us,
                mean_us=mean_us,
                stdev_us=std_us,
                cov=(std_us / mean_us) if mean_us > 1e-12 else 0.0,
            ),
            env={
                "backend": "kernelbench",
                "kernelbench_backend": self.config.backend,
                "precision": self.config.precision,
                "dataset_source": self.config.dataset_source,
                "problem_level": self.config.level,
                "problem_id": self.config.problem_id,
            },
            profile={
                "runtime_unit": "ms",
                "timing_estimate": "median_us uses kernelbench mean; p95_us uses kernelbench max",
                "runtime_stats": dict(eval_result.runtime_stats),
                "ref_runtime_ms": ref_runtime_ms,
                "ref_runtime_stats": ref_runtime_stats,
                "speedup_vs_ref": speedup_vs_ref,
                "metadata": dict(eval_result.metadata),
            },
        )

    def score(self, benchmark: BenchmarkResult, validation: ValidationResult) -> float | dict[str, float]:
        if validation.status is not ValidationStatus.PASS:
            return {"fitness": -1e18, "valid": 0.0}
        if benchmark.status is not BenchmarkStatus.SUCCESS:
            return {"fitness": -1e18, "valid": 1.0}

        latency_us = max(benchmark.timing.median_us, 1e-9)
        fitness = 1_000_000.0 / latency_us
        payload: dict[str, float] = {
            "fitness": fitness,
            "median_us": benchmark.timing.median_us,
            "valid": 1.0,
        }

        speedup_vs_ref = self._as_float(benchmark.profile.get("speedup_vs_ref"), default=-1.0)
        if speedup_vs_ref <= 0.0:
            ref_runtime_ms = self._as_float(benchmark.profile.get("ref_runtime_ms"), default=-1.0)
            if ref_runtime_ms > 0:
                speedup_vs_ref = (ref_runtime_ms * 1_000.0) / latency_us
        if speedup_vs_ref > 0.0:
            payload["speedup_vs_ref"] = speedup_vs_ref
        return payload

    def describe(
        self,
        candidate: Candidate,
        build: BuildExecution,
        benchmark: BenchmarkResult,
    ) -> Descriptor:
        compiler_metrics = build.result.compiler_metrics
        source = self._candidate_source(candidate)

        regs = int(compiler_metrics.get("registers_per_thread", max(0, min(96, len(source) // 140))))
        smem = int(compiler_metrics.get("smem_static_bytes", 0)) + int(compiler_metrics.get("smem_dynamic_bytes", 0))
        occupancy = self._as_float(compiler_metrics.get("occupancy_estimate"), default=self._occupancy_proxy(source))

        reg_bin = 0 if regs < 32 else 1 if regs < 48 else 2 if regs < 64 else 3
        smem_bin = 0 if smem == 0 else 1 if smem < 16_384 else 2 if smem < 48_000 else 3
        occ_bin = 0 if occupancy < 0.25 else 1 if occupancy < 0.5 else 2 if occupancy < 0.75 else 3

        source_len = len(source)
        source_len_bin = 0 if source_len < 1_500 else 1 if source_len < 4_000 else 2 if source_len < 10_000 else 3
        block_x = int(candidate.representation.launch.block[0]) if candidate.representation.launch.block else 256
        launch_block_bin = max(0, min(7, int((max(32, min(1024, block_x)) - 32) / 128)))
        source_ops_score = sum(
            source.count(token)
            for token in ("for ", "while ", "if ", "torch.", "triton", "threadIdx", "blockIdx", "__global__")
        )
        source_ops_bin = max(0, min(7, source_ops_score))

        return Descriptor(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            descriptor_name="kernelbench_v1",
            values={
                "reg_pressure_bin": reg_bin,
                "smem_bin": smem_bin,
                "occupancy_bin": occ_bin,
                "launch_block_bin": launch_block_bin,
                "source_ops_bin": source_ops_bin,
                "source_len_bin": source_len_bin,
                "problem_level": self.config.level,
                "problem_id": self.config.problem_id,
                "stage_full": 1 if benchmark.stage is BenchmarkStage.FULL else 0,
            },
        )

    def _load_kernelbench_modules(self) -> tuple[Any, Any, Any]:
        self._ensure_repo_path()
        try:
            kb_eval = importlib.import_module("kernelbench.eval")
            kb_dataset = importlib.import_module("kernelbench.dataset")
            torch_module = importlib.import_module("torch")
        except Exception as exc:
            raise RuntimeError(
                "KernelBench dependencies are unavailable. "
                "Install KernelBench + torch in this environment or pass repo_path in problem_config."
            ) from exc

        if not bool(torch_module.cuda.is_available()):
            raise RuntimeError("CUDA is not available; KernelBench requires an NVIDIA GPU")

        return kb_eval, kb_dataset, torch_module

    def _resolve_reference_source(self, kb_dataset_module: Any) -> tuple[str, str]:
        dataset_kwargs: dict[str, Any] = {
            "level": self.config.level,
            "source": self.config.dataset_source,
            "dataset_name": self.config.dataset_name,
        }
        if self.config.dataset_base_path:
            dataset_kwargs["base_path"] = self.config.dataset_base_path
        elif self.config.repo_path and self.config.dataset_source == "local":
            dataset_kwargs["base_path"] = str(Path(self.config.repo_path).expanduser() / "KernelBench")

        dataset = kb_dataset_module.construct_kernelbench_dataset(**dataset_kwargs)
        problem = dataset.get_problem_by_id(int(self.config.problem_id))
        return str(problem.code), str(problem.name)

    def _get_static_runtime(self) -> _KernelBenchStaticRuntime:
        cached = self._static_runtime
        if cached is not None:
            return cached

        with self._static_runtime_lock:
            cached = self._static_runtime
            if cached is not None:
                return cached
            kb_eval, kb_dataset, _torch_module = self._load_kernelbench_modules()
            ref_source, ref_name = self._resolve_reference_source(kb_dataset)
            precision_dtype = kb_eval.get_torch_dtype_from_string(self.config.precision)
            cached = _KernelBenchStaticRuntime(
                kb_eval_module=kb_eval,
                ref_source=ref_source,
                ref_name=ref_name,
                precision_dtype=precision_dtype,
            )
            self._static_runtime = cached
            return cached

    @staticmethod
    def _detect_backend(candidate_source: str, configured_backend: str) -> str:
        """Auto-detect the correct KernelBench backend for the candidate.

        Triton kernels use ``@triton.jit`` which internally calls
        ``inspect.getsource()`` — that requires the code to live in a real
        ``.py`` file.  KernelBench already handles this when
        ``backend="triton"`` (writes to a tempfile and imports the module),
        but the swarm may be configured with ``backend="cuda"``.  Detect
        Triton usage and override so the tempfile path is taken.
        """
        if configured_backend.lower() in ("triton", "tilelang", "cute"):
            return configured_backend  # already correct
        # Heuristic: if the source imports triton or uses the @triton.jit decorator,
        # it needs the tempfile-based loader.
        if re.search(r'\bimport\s+triton\b', candidate_source) or re.search(r'@triton\.jit\b', candidate_source):
            return "triton"
        return configured_backend

    def _evaluate_kernel(
        self,
        *,
        kb_eval: Any,
        ref_source: str,
        candidate_source: str,
        build_dir: Path,
        device: int,
        precision_dtype: Any,
        measure_performance: bool,
        num_correct_trials: int,
        num_perf_trials: int,
    ) -> _KernelBenchEvalResult:
        eval_backend = self._detect_backend(candidate_source, self.config.backend)
        if eval_backend != self.config.backend:
            _log.info(
                "Auto-detected backend %r for candidate (configured: %r)",
                eval_backend,
                self.config.backend,
            )
        raw = kb_eval.eval_kernel_against_ref(
            original_model_src=ref_source,
            custom_model_src=candidate_source,
            num_correct_trials=num_correct_trials,
            num_perf_trials=num_perf_trials,
            measure_performance=measure_performance,
            timing_method=self.config.timing_method,
            verbose=self.config.verbose,
            build_dir=str(build_dir),
            device=device,
            backend=eval_backend,
            precision=precision_dtype,
            check_for_excessive_speedup=False,
        )
        if raw is None:
            raise RuntimeError("KernelBench eval returned None; likely transient compile-lock contention")
        return _KernelBenchEvalResult(
            compiled=bool(getattr(raw, "compiled", False)),
            correctness=bool(getattr(raw, "correctness", False)),
            runtime_ms=self._as_float(getattr(raw, "runtime", -1.0), default=-1.0),
            runtime_stats=self._json_safe_dict(getattr(raw, "runtime_stats", {})),
            ref_runtime_ms=self._as_float(getattr(raw, "ref_runtime", -1.0), default=-1.0),
            ref_runtime_stats=self._json_safe_dict(getattr(raw, "ref_runtime_stats", {})),
            metadata=self._json_safe_dict(getattr(raw, "metadata", {})),
        )

    def _resolve_reference_runtime(
        self,
        *,
        runtime: _KernelBenchRuntime,
        eval_result: _KernelBenchEvalResult,
        stage: BenchmarkStage,
    ) -> tuple[float, dict[str, Any]]:
        ref_runtime_stats = self._json_safe_dict(eval_result.ref_runtime_stats)
        ref_runtime_ms = self._as_float(eval_result.ref_runtime_ms, default=-1.0)
        if ref_runtime_ms <= 0.0:
            ref_runtime_ms = self._as_float(ref_runtime_stats.get("mean"), default=-1.0)
        if ref_runtime_ms > 0.0:
            return ref_runtime_ms, ref_runtime_stats

        num_correct = max(
            1,
            self.config.quick_correct_trials if stage is BenchmarkStage.QUICK else self.config.full_correct_trials,
        )
        num_perf = max(
            1,
            self.config.quick_perf_trials if stage is BenchmarkStage.QUICK else self.config.full_perf_trials,
        )
        return self._reference_runtime_stats(
            kb_eval=runtime.kb_eval_module,
            ref_source=runtime.ref_source,
            device=int(runtime.device),
            precision_dtype=runtime.precision_dtype,
            num_correct_trials=num_correct,
            num_perf_trials=num_perf,
        )

    def _reference_runtime_stats(
        self,
        *,
        kb_eval: Any,
        ref_source: str,
        device: int,
        precision_dtype: Any,
        num_correct_trials: int,
        num_perf_trials: int,
    ) -> tuple[float, dict[str, Any]]:
        key = (int(num_correct_trials), int(num_perf_trials))
        with self._ref_runtime_lock:
            cached = self._ref_runtime_cache.get(key)
            if cached is not None:
                return cached

            try:
                ref_as_candidate = (
                    ref_source
                    .replace("class Model(", "class ModelNew(")
                    .replace("super(Model,", "super(ModelNew,")
                    .replace("super(Model ,", "super(ModelNew ,")
                )
                ref_eval = self._evaluate_kernel(
                    kb_eval=kb_eval,
                    ref_source=ref_source,
                    candidate_source=ref_as_candidate,
                    build_dir=self._reference_build_dir(
                        num_correct_trials=num_correct_trials,
                        num_perf_trials=num_perf_trials,
                    ),
                    device=device,
                    precision_dtype=precision_dtype,
                    measure_performance=True,
                    num_correct_trials=max(1, int(num_correct_trials)),
                    num_perf_trials=max(1, int(num_perf_trials)),
                )
                ref_runtime_stats = self._json_safe_dict(ref_eval.runtime_stats)
                ref_runtime_ms = self._as_float(ref_eval.runtime_ms, default=-1.0)
                if ref_runtime_ms <= 0.0:
                    ref_runtime_ms = self._as_float(ref_runtime_stats.get("mean"), default=-1.0)
            except Exception as exc:
                _log.warning("reference runtime benchmark failed: %s", exc, exc_info=True)
                return -1.0, {}

            cached = (ref_runtime_ms, ref_runtime_stats)
            self._ref_runtime_cache[key] = cached
            return cached

    @staticmethod
    def _has_performance_stats(eval_result: _KernelBenchEvalResult) -> bool:
        if eval_result.runtime_ms > 0:
            return True
        if not isinstance(eval_result.runtime_stats, dict):
            return False
        mean = eval_result.runtime_stats.get("mean")
        if isinstance(mean, (int, float)) and float(mean) > 0:
            return True
        return False

    def _make_candidate(
        self,
        *,
        run_id: str,
        source: str,
        operation: str,
        agent_id: str,
        hypothesis: str,
    ) -> Candidate:
        rep = CandidateRepresentation(
            language="python",
            entrypoints=["ModelNew"],
            files=[SourceFile(path="model_new.py", content=source)],
            params={
                "kernelbench_level": self.config.level,
                "kernelbench_problem_id": self.config.problem_id,
                "kernelbench_backend": self.config.backend,
                "kernelbench_precision": self.config.precision,
            },
            launch=LaunchConfig(grid=("auto", 1, 1), block=(256, 1, 1), dynamic_smem_bytes=0),
            compile=CompileConfig(arch="auto", flags=[], defines={}),
        )
        return Candidate.new(
            run_id=run_id,
            parent_ids=[],
            origin=CandidateOrigin(island_id="island-a", agent_id=agent_id, operation=operation),
            representation=rep,
            track="from_scratch",
            hypothesis=hypothesis,
        )

    def _build_dir_for(self, candidate: Candidate) -> Path:
        root = (
            Path(self.config.build_dir_root).expanduser()
            if self.config.build_dir_root
            else Path(tempfile.gettempdir()) / "kernelswarm_kernelbench_builds"
        )
        stable = candidate.content_hash or sha256_text(self._candidate_source(candidate))
        path = root / f"level_{self.config.level}" / f"problem_{self.config.problem_id}" / stable[:24]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _reference_build_dir(self, *, num_correct_trials: int, num_perf_trials: int) -> Path:
        root = (
            Path(self.config.build_dir_root).expanduser()
            if self.config.build_dir_root
            else Path(tempfile.gettempdir()) / "kernelswarm_kernelbench_builds"
        )
        path = (
            root
            / "reference_baseline"
            / f"level_{self.config.level}"
            / f"problem_{self.config.problem_id}"
            / f"backend_{self.config.backend}"
            / f"precision_{self.config.precision}"
            / f"c{int(num_correct_trials)}_p{int(num_perf_trials)}"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _candidate_source(self, candidate: Candidate) -> str:
        if not candidate.representation.files:
            return ""
        for file in candidate.representation.files:
            if file.path.endswith(".py"):
                return str(file.content)
        return str(candidate.representation.files[0].content)

    @staticmethod
    def _validation_summary(metadata: dict[str, Any]) -> str:
        if not metadata:
            return "KernelBench correctness failed"

        parts: list[str] = []

        for key in (
            "correctness_issue",
            "runtime_error",
            "runtime_error_name",
            "compilation_error",
            "compilation_error_name",
            "other_error",
            "cuda_error",
        ):
            if key in metadata:
                parts.append(f"{key}={metadata[key]}")
                break

        if not parts:
            first_key = sorted(metadata.keys())[0]
            parts.append(f"{first_key}={metadata[first_key]}")

        # Append numerical error diagnostics when available so the LLM
        # understands *how wrong* the output was (not just "mismatch").
        max_diffs = metadata.get("max_difference")
        avg_diffs = metadata.get("avg_difference")
        if max_diffs:
            val = max_diffs[0] if isinstance(max_diffs, list) else max_diffs
            parts.append(f"max_abs_diff={val}")
        if avg_diffs:
            val = avg_diffs[0] if isinstance(avg_diffs, list) else avg_diffs
            parts.append(f"avg_abs_diff={val}")

        corr_trials = metadata.get("correctness_trials")
        if corr_trials:
            parts.append(f"trials={corr_trials}")

        return "; ".join(parts)

    @staticmethod
    def _compiler_metrics_from_eval(eval_result: _KernelBenchEvalResult) -> dict[str, int | float]:
        out: dict[str, int | float] = {}
        for key in (
            "registers_per_thread",
            "smem_static_bytes",
            "smem_dynamic_bytes",
            "spill_stores",
            "spill_loads",
            "occupancy_estimate",
        ):
            value = eval_result.metadata.get(key)
            if value is None:
                continue
            if isinstance(value, bool):
                out[key] = int(value)
                continue
            if isinstance(value, (int, float)):
                out[key] = float(value) if isinstance(value, float) else int(value)
                continue
            try:
                numeric = float(value)
                out[key] = int(numeric) if numeric.is_integer() else numeric
            except (TypeError, ValueError):
                continue
        return out

    def _kernelbench_version(self) -> str:
        try:
            return importlib.metadata.version("kernelbench")
        except Exception:
            return "unknown"

    def _ensure_repo_path(self) -> None:
        if not self.config.repo_path:
            return
        src_path = Path(self.config.repo_path).expanduser() / "src"
        if src_path.exists():
            src_str = str(src_path)
            if src_str not in sys.path:
                sys.path.insert(0, src_str)

    @staticmethod
    def _json_safe_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        safe: dict[str, Any] = {}
        for key, raw in value.items():
            safe[str(key)] = KernelBenchProblem._json_safe(raw)
        return safe

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [KernelBenchProblem._json_safe(v) for v in value]
        if isinstance(value, tuple):
            return [KernelBenchProblem._json_safe(v) for v in value]
        if isinstance(value, dict):
            return {str(k): KernelBenchProblem._json_safe(v) for k, v in value.items()}
        return str(value)

    @staticmethod
    def _as_float(value: Any, *, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _occupancy_proxy(source: str) -> float:
        cuda_markers = sum(source.count(token) for token in ("__global__", "threadIdx", "blockIdx", "load_inline", "triton"))
        if cuda_markers <= 0:
            return 0.25
        if cuda_markers <= 2:
            return 0.4
        if cuda_markers <= 5:
            return 0.6
        return 0.8

    @staticmethod
    def _seed_sources() -> list[str]:
        return [
            # Seed 0 (baseline): trivial wrapper — establishes reference fitness.
            # We intentionally do NOT seed with torch.compile variants. When compile
            # seeds dominate the archive at ~4.7x, the LLM is forced to write custom
            # Triton to beat them and fails 90%+ of the time.  Starting from the raw
            # reference lets the LLM discover torch.compile on its own as an easy win
            # AND attempt simpler custom kernels against a lower bar.
            (
                "import torch\n"
                "import torch.nn as nn\n\n"
                "class ModelNew(nn.Module):\n"
                "    def __init__(self, *init_inputs):\n"
                "        super().__init__()\n"
                "        self._impl = Model(*init_inputs)\n\n"
                "    def forward(self, *inputs):\n"
                "        return self._impl(*inputs)\n"
            ),
        ]
