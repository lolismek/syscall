from __future__ import annotations

import unittest
from unittest import mock

from kernelswarm.models import BenchmarkStage, BuildStatus, ValidationStatus
from kernelswarm.plugins.kernelbench import (
    KernelBenchConfig,
    KernelBenchProblem,
    _KernelBenchEvalResult,
)
from kernelswarm.registry import default_problem_factories
from kernelswarm.sdk import ProblemRunContext


class _FakeKernelEvalModule:
    @staticmethod
    def get_torch_dtype_from_string(precision: str) -> str:
        return f"dtype:{precision}"


class KernelBenchPluginTests(unittest.TestCase):
    def test_registry_exposes_kernelbench_problem(self) -> None:
        factories = default_problem_factories()
        self.assertIn("kernelbench_v1", factories)

    def test_static_check_requires_modelnew(self) -> None:
        problem = KernelBenchProblem(KernelBenchConfig(static_check_enabled=False))
        ctx = ProblemRunContext(run_id="run-static", seed=1)
        candidate = problem.baseline(ctx)
        assert candidate is not None
        candidate.representation.files[0].content = "class SomethingElse:\n    pass\n"

        static = problem.static_check(candidate)
        self.assertFalse(static.ok)
        self.assertIn("candidate source must define ModelNew", static.reasons)

    def test_build_reports_infra_error_when_kernelbench_import_fails(self) -> None:
        problem = KernelBenchProblem(KernelBenchConfig())
        ctx = ProblemRunContext(run_id="run-infra", seed=2)
        candidate = problem.baseline(ctx)
        assert candidate is not None

        with mock.patch.object(problem, "_load_kernelbench_modules", side_effect=RuntimeError("missing deps")):
            build = problem.build(candidate)

        self.assertEqual(build.result.status, BuildStatus.INFRA_ERROR)
        self.assertIsNone(build.runtime)

    def test_build_validate_benchmark_score_describe_happy_path(self) -> None:
        problem = KernelBenchProblem(
            KernelBenchConfig(
                static_check_enabled=False,
                quick_correct_trials=1,
                quick_perf_trials=4,
                full_correct_trials=1,
                full_perf_trials=6,
            )
        )
        ctx = ProblemRunContext(run_id="run-ok", seed=3)
        candidate = problem.baseline(ctx)
        assert candidate is not None

        build_eval = _KernelBenchEvalResult(
            compiled=True,
            correctness=True,
            runtime_ms=-1.0,
            runtime_stats={},
            ref_runtime_ms=-1.0,
            ref_runtime_stats={},
            metadata={},
        )
        quick_eval = _KernelBenchEvalResult(
            compiled=True,
            correctness=True,
            runtime_ms=0.33,
            runtime_stats={"mean": 0.33, "std": 0.02, "max": 0.38, "num_trials": 4},
            ref_runtime_ms=0.66,
            ref_runtime_stats={"mean": 0.66},
            metadata={"hardware": "FakeGPU"},
        )
        full_eval = _KernelBenchEvalResult(
            compiled=True,
            correctness=True,
            runtime_ms=0.20,
            runtime_stats={"mean": 0.20, "std": 0.01, "max": 0.22, "num_trials": 6},
            ref_runtime_ms=0.65,
            ref_runtime_stats={"mean": 0.65},
            metadata={"hardware": "FakeGPU"},
        )

        with (
            mock.patch.object(
                problem,
                "_load_kernelbench_modules",
                return_value=(_FakeKernelEvalModule(), object(), object()),
            ),
            mock.patch.object(problem, "_resolve_reference_source", return_value=("class Model: pass", "p1.py")),
            mock.patch.object(problem, "_evaluate_kernel", side_effect=[build_eval, quick_eval, full_eval]),
        ):
            build = problem.build(candidate)
            self.assertEqual(build.result.status, BuildStatus.SUCCESS)

            validation = problem.validate(candidate, build)
            self.assertEqual(validation.status, ValidationStatus.PASS)

            bench_quick = problem.benchmark(candidate, build, BenchmarkStage.QUICK)
            self.assertEqual(bench_quick.samples, 4)

            quick_score = problem.score(bench_quick, validation)
            self.assertIsInstance(quick_score, dict)
            assert isinstance(quick_score, dict)
            self.assertGreater(float(quick_score["fitness"]), 0.0)
            self.assertGreater(float(quick_score["speedup_vs_ref"]), 1.0)

            descriptor = problem.describe(candidate, build, bench_quick)
            self.assertIn("reg_pressure_bin", descriptor.values)
            self.assertIn("smem_bin", descriptor.values)
            self.assertIn("occupancy_bin", descriptor.values)

            bench_full = problem.benchmark(candidate, build, BenchmarkStage.FULL)
            self.assertEqual(bench_full.samples, 6)

    def test_benchmark_resolves_reference_runtime_when_missing_from_eval(self) -> None:
        problem = KernelBenchProblem(
            KernelBenchConfig(
                static_check_enabled=False,
                quick_correct_trials=1,
                quick_perf_trials=4,
                full_correct_trials=1,
                full_perf_trials=8,
            )
        )
        ctx = ProblemRunContext(run_id="run-ref-fallback", seed=9)
        candidate = problem.baseline(ctx)
        assert candidate is not None

        build_eval = _KernelBenchEvalResult(
            compiled=True,
            correctness=True,
            runtime_ms=0.45,
            runtime_stats={"mean": 0.45, "std": 0.01, "max": 0.47, "num_trials": 4},
            ref_runtime_ms=-1.0,
            ref_runtime_stats={},
            metadata={},
        )
        full_eval = _KernelBenchEvalResult(
            compiled=True,
            correctness=True,
            runtime_ms=0.40,
            runtime_stats={"mean": 0.40, "std": 0.02, "max": 0.45, "num_trials": 8},
            ref_runtime_ms=-1.0,
            ref_runtime_stats={},
            metadata={},
        )
        ref_eval = _KernelBenchEvalResult(
            compiled=True,
            correctness=True,
            runtime_ms=0.80,
            runtime_stats={"mean": 0.80, "std": 0.01, "max": 0.82, "num_trials": 8},
            ref_runtime_ms=-1.0,
            ref_runtime_stats={},
            metadata={},
        )

        with (
            mock.patch.object(
                problem,
                "_load_kernelbench_modules",
                return_value=(_FakeKernelEvalModule(), object(), object()),
            ),
            mock.patch.object(problem, "_resolve_reference_source", return_value=("class Model: pass", "p1.py")),
            mock.patch.object(problem, "_evaluate_kernel", side_effect=[build_eval, full_eval, ref_eval]),
        ):
            build = problem.build(candidate)
            self.assertEqual(build.result.status, BuildStatus.SUCCESS)
            validation = problem.validate(candidate, build)
            self.assertEqual(validation.status, ValidationStatus.PASS)

            bench_full = problem.benchmark(candidate, build, BenchmarkStage.FULL)
            self.assertEqual(bench_full.status.name, "SUCCESS")
            self.assertAlmostEqual(float(bench_full.profile["ref_runtime_ms"]), 0.80, places=6)
            self.assertGreater(float(bench_full.profile["speedup_vs_ref"]), 1.9)

            score = problem.score(bench_full, validation)
            assert isinstance(score, dict)
            self.assertGreater(float(score["speedup_vs_ref"]), 1.9)


if __name__ == "__main__":
    unittest.main()
