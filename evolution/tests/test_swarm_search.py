from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kernelswarm.plugins.vector_add import VectorAddConfig, VectorAddProblem
from kernelswarm.search import SearchConfig, SwarmSearchRunner


class SwarmSearchTests(unittest.TestCase):
    def test_search_runs_and_resume_from_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            checkpoint = workspace / "checkpoint.json"
            problem = VectorAddProblem(
                VectorAddConfig(
                    backend="python-sim",
                    seed_count=2,
                    quick_size=1_024,
                    full_size=2_048,
                    quick_iters=2,
                    full_iters=3,
                    quick_warmup=1,
                    full_warmup=1,
                )
            )

            first = SwarmSearchRunner(
                SearchConfig(
                    workspace=workspace,
                    max_iterations=6,
                    max_minutes=1.0,
                    llm_enabled=False,
                    checkpoint_path=checkpoint,
                    migration_every_updates=2,
                    checkpoint_every_iterations=2,
                )
            ).run(problem)
            self.assertTrue(checkpoint.exists())
            self.assertIsNotNone(first.best_candidate_id)

            second = SwarmSearchRunner(
                SearchConfig(
                    workspace=workspace,
                    max_iterations=8,
                    max_minutes=1.0,
                    llm_enabled=False,
                    checkpoint_path=checkpoint,
                    resume=True,
                    migration_every_updates=2,
                    checkpoint_every_iterations=2,
                )
            ).run(problem)
            self.assertEqual(first.run_id, second.run_id)
            self.assertGreaterEqual(second.total_candidates, first.total_candidates)

            report = json.loads(Path(second.report_path).read_text(encoding="utf-8"))
            self.assertIn("islands", report)
            self.assertGreaterEqual(report["iterations_completed"], 6)


if __name__ == "__main__":
    unittest.main()
