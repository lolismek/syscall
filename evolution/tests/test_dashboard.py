from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kernelswarm.dashboard import DashboardService
from kernelswarm.plugins.vector_add import VectorAddConfig, VectorAddProblem
from kernelswarm.search import SearchConfig, SwarmSearchRunner


class DashboardTests(unittest.TestCase):
    def test_dashboard_service_returns_run_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            problem = VectorAddProblem(
                VectorAddConfig(
                    backend="python-sim",
                    seed_count=2,
                    quick_size=1024,
                    full_size=2048,
                    quick_iters=2,
                    full_iters=3,
                    quick_warmup=1,
                    full_warmup=1,
                )
            )
            summary = SwarmSearchRunner(
                SearchConfig(
                    workspace=workspace,
                    max_iterations=4,
                    max_minutes=1.0,
                    llm_enabled=False,
                    checkpoint_every_iterations=1,
                    migration_every_updates=2,
                )
            ).run(problem)

            service = DashboardService(workspace)
            runs = service.list_runs()
            self.assertTrue(any(row["run_id"] == summary.run_id for row in runs))

            overview = service.run_overview(summary.run_id)
            self.assertEqual(overview["run_id"], summary.run_id)
            self.assertIn("state_counts", overview)

            quick = service.leaderboard(summary.run_id, stage="quick", limit=5)
            self.assertGreaterEqual(len(quick), 1)

            states = service.state_snapshot(summary.run_id)
            self.assertIn("state_counts", states)
            self.assertIn("transitions", states)

            ts = service.timeseries(summary.run_id)
            self.assertEqual(ts["run_id"], summary.run_id)
            self.assertGreaterEqual(len(ts["global"]), 1)
            self.assertEqual(len(ts["islands"].keys()), 4)

            global_points = ts["global"]
            fitness_values = [float(p["global_best_fitness"]) for p in global_points if p.get("global_best_fitness") is not None]
            median_values = [float(p["global_best_median_us"]) for p in global_points if p.get("global_best_median_us") is not None]
            token_values = [int(p["total_tokens"]) for p in global_points if p.get("total_tokens") is not None]

            self.assertTrue(
                all(cur >= prev for prev, cur in zip(fitness_values, fitness_values[1:])),
                "global_best_fitness should be monotonic non-decreasing",
            )
            self.assertTrue(
                all(cur <= prev for prev, cur in zip(median_values, median_values[1:])),
                "global_best_median_us should be monotonic non-increasing",
            )
            self.assertTrue(
                all(cur >= prev for prev, cur in zip(token_values, token_values[1:])),
                "total_tokens should be monotonic non-decreasing",
            )


if __name__ == "__main__":
    unittest.main()
