from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kernelswarm.models import BuildExecution, BuildResult, BuildStatus
from kernelswarm.plugins.vector_add import VectorAddConfig, VectorAddProblem
from kernelswarm.search import SearchConfig, SwarmSearchRunner


class _AlwaysFailBuildVectorAddProblem(VectorAddProblem):
    def build(self, candidate):  # type: ignore[override]
        result = BuildResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            status=BuildStatus.FAILURE,
            build_backend="test-failure",
            duration_ms=1,
            stderr_digest="test-build-failure",
            artifacts={},
            compiler_metrics={},
            toolchain_fingerprint={"backend": "test-failure"},
        )
        return BuildExecution(result=result, runtime=None)


class _BuildMustNotRunProblem(VectorAddProblem):
    def build(self, candidate):  # type: ignore[override]
        raise AssertionError("local build should not run when remote eval is configured")


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

    def test_build_failures_do_not_leave_candidates_queued_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            problem = _AlwaysFailBuildVectorAddProblem(
                VectorAddConfig(
                    backend="python-sim",
                    seed_count=1,
                    quick_size=256,
                    full_size=512,
                    quick_iters=1,
                    full_iters=1,
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
                    checkpoint_every_iterations=2,
                )
            ).run(problem)
            self.assertIsNotNone(summary.run_id)

            import sqlite3

            db_path = workspace / "db" / "runs.sqlite"
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT state, COUNT(*)
                    FROM candidates
                    WHERE run_id = ?
                    GROUP BY state
                    ORDER BY state
                    """,
                    (summary.run_id,),
                ).fetchall()

            states = {str(state): int(count) for state, count in rows}
            self.assertNotIn("QUEUED_BUILD", states)
            self.assertGreater(states.get("SCORED", 0), 0)

    def test_remote_eval_error_does_not_fallback_to_local_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            problem = _BuildMustNotRunProblem(
                VectorAddConfig(
                    backend="python-sim",
                    seed_count=1,
                    quick_size=256,
                    full_size=512,
                    quick_iters=1,
                    full_iters=1,
                    quick_warmup=1,
                    full_warmup=1,
                )
            )

            summary = SwarmSearchRunner(
                SearchConfig(
                    workspace=workspace,
                    max_iterations=2,
                    max_minutes=1.0,
                    llm_enabled=False,
                    remote_eval_url="http://127.0.0.1:1",
                    remote_eval_timeout_s=1.0,
                )
            ).run(problem)
            self.assertIsNotNone(summary.run_id)

            import sqlite3

            db_path = workspace / "db" / "runs.sqlite"
            with sqlite3.connect(db_path) as conn:
                build_count = conn.execute(
                    "SELECT COUNT(*) FROM build_results WHERE run_id = ?",
                    (summary.run_id,),
                ).fetchone()[0]
                reason_rows = conn.execute(
                    """
                    SELECT json_extract(payload_json, '$.raw_score.reason')
                    FROM scores
                    WHERE run_id = ? AND stage = 'quick'
                    """,
                    (summary.run_id,),
                ).fetchall()

            self.assertEqual(int(build_count), 0)
            reasons = {str(row[0]) for row in reason_rows}
            self.assertIn("remote_eval_error", reasons)

    def test_remote_eval_url_fanout_list_does_not_fallback_to_local_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            problem = _BuildMustNotRunProblem(
                VectorAddConfig(
                    backend="python-sim",
                    seed_count=1,
                    quick_size=256,
                    full_size=512,
                    quick_iters=1,
                    full_iters=1,
                    quick_warmup=1,
                    full_warmup=1,
                )
            )

            summary = SwarmSearchRunner(
                SearchConfig(
                    workspace=workspace,
                    max_iterations=2,
                    max_minutes=1.0,
                    llm_enabled=False,
                    remote_eval_url="http://127.0.0.1:1,http://127.0.0.1:2",
                    remote_eval_timeout_s=1.0,
                )
            ).run(problem)
            self.assertIsNotNone(summary.run_id)

            import sqlite3

            db_path = workspace / "db" / "runs.sqlite"
            with sqlite3.connect(db_path) as conn:
                build_count = conn.execute(
                    "SELECT COUNT(*) FROM build_results WHERE run_id = ?",
                    (summary.run_id,),
                ).fetchone()[0]
                reason_rows = conn.execute(
                    """
                    SELECT json_extract(payload_json, '$.raw_score.reason')
                    FROM scores
                    WHERE run_id = ? AND stage = 'quick'
                    """,
                    (summary.run_id,),
                ).fetchall()

            self.assertEqual(int(build_count), 0)
            reasons = {str(row[0]) for row in reason_rows}
            self.assertIn("remote_eval_error", reasons)

    def test_summary_best_tracks_full_stage_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            problem = VectorAddProblem(
                VectorAddConfig(
                    backend="python-sim",
                    seed_count=2,
                    quick_size=1024,
                    full_size=8192,
                    quick_iters=2,
                    full_iters=4,
                    quick_warmup=1,
                    full_warmup=1,
                )
            )

            summary = SwarmSearchRunner(
                SearchConfig(
                    workspace=workspace,
                    max_iterations=24,
                    max_minutes=1.0,
                    llm_enabled=False,
                    force_first_full_per_island=True,
                    periodic_full_eval_every_quick=4,
                    proposal_workers=4,
                    quick_eval_workers=2,
                    full_eval_workers=2,
                    max_inflight_proposals=24,
                    max_inflight_quick_evals=8,
                    max_inflight_full_evals=4,
                )
            ).run(problem)
            self.assertIsNotNone(summary.run_id)
            self.assertIsNotNone(summary.best_candidate_id)
            self.assertIsNotNone(summary.best_fitness)

            import sqlite3

            db_path = workspace / "db" / "runs.sqlite"
            with sqlite3.connect(db_path) as conn:
                full_top = conn.execute(
                    """
                    SELECT candidate_id, scalar_fitness
                    FROM scores
                    WHERE run_id = ? AND stage = 'full'
                    ORDER BY scalar_fitness DESC
                    LIMIT 1
                    """,
                    (summary.run_id,),
                ).fetchone()
                self.assertIsNotNone(full_top)

                best_full_row = conn.execute(
                    """
                    SELECT scalar_fitness
                    FROM scores
                    WHERE run_id = ? AND candidate_id = ? AND stage = 'full'
                    """,
                    (summary.run_id, summary.best_candidate_id),
                ).fetchone()
                self.assertIsNotNone(best_full_row)

            assert full_top is not None
            assert best_full_row is not None
            self.assertAlmostEqual(float(summary.best_fitness), float(full_top[1]), places=8)
            self.assertAlmostEqual(float(best_full_row[0]), float(full_top[1]), places=8)


if __name__ == "__main__":
    unittest.main()
