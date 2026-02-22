from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelswarm.pipeline import PipelineConfig, SingleWorkerPipeline
from kernelswarm.plugins.vector_add import VectorAddConfig, VectorAddProblem


class VectorAddPipelineTests(unittest.TestCase):
    def test_pipeline_generates_run_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            problem = VectorAddProblem(
                VectorAddConfig(
                    quick_size=2_000,
                    full_size=4_000,
                    quick_warmup=1,
                    quick_iters=3,
                    full_warmup=1,
                    full_iters=4,
                    seed_count=3,
                )
            )
            pipeline = SingleWorkerPipeline(
                PipelineConfig(
                    workspace=workspace,
                    seed=123,
                    full_benchmark_top_k=1,
                )
            )
            summary = pipeline.run(problem)

            self.assertEqual(summary.problem_id, "vector_add_v1")
            self.assertGreaterEqual(summary.total_candidates, 2)
            self.assertIsNotNone(summary.best_candidate_id)
            self.assertTrue(Path(summary.report_path).exists())
            self.assertTrue((workspace / "db" / "runs.sqlite").exists())

    def test_nvcc_arch_resolution_prefers_explicit_arch(self) -> None:
        problem = VectorAddProblem(VectorAddConfig(backend="nvcc"))
        self.assertEqual(problem._resolve_nvcc_arch("sm_80"), "sm_80")

    def test_nvcc_arch_resolution_detects_gpu_compute_cap(self) -> None:
        problem = VectorAddProblem(VectorAddConfig(backend="nvcc"))
        with (
            mock.patch("kernelswarm.plugins.vector_add.shutil.which", return_value="/usr/bin/nvidia-smi"),
            mock.patch.object(VectorAddProblem, "_capture_cmd", return_value="7.5"),
        ):
            self.assertEqual(problem._resolve_nvcc_arch("auto"), "sm_75")

    def test_nvcc_arch_resolution_falls_back_when_detection_unavailable(self) -> None:
        problem = VectorAddProblem(VectorAddConfig(backend="nvcc"))
        with (
            mock.patch("kernelswarm.plugins.vector_add.shutil.which", return_value=None),
            mock.patch.object(VectorAddProblem, "_capture_cmd", return_value=None),
        ):
            self.assertEqual(problem._resolve_nvcc_arch("auto"), "sm_75")


if __name__ == "__main__":
    unittest.main()
