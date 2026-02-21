from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import (
    BenchmarkResult,
    BuildResult,
    Candidate,
    CandidateState,
    Descriptor,
    IterationMetric,
    RunManifest,
    RunSummary,
    ScoreRecord,
    ValidationResult,
)
from .serialization import to_json


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("PRAGMA busy_timeout=30000;")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec_write(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(query, params)
            self._conn.commit()

    def _exec_writes(self, operations: list[tuple[str, tuple[Any, ...]]]) -> None:
        with self._lock:
            for query, params in operations:
                self._conn.execute(query, params)
            self._conn.commit()

    def _exec_read_one(self, query: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
        with self._lock:
            cursor = self._conn.execute(query, params)
            return cursor.fetchone()

    def _exec_read_all(self, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            cursor = self._conn.execute(query, params)
            return list(cursor.fetchall())

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    problem_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    summary_json TEXT
                );

                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS state_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS build_results (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id)
                );

                CREATE TABLE IF NOT EXISTS validation_results (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id)
                );

                CREATE TABLE IF NOT EXISTS benchmark_results (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id, stage)
                );

                CREATE TABLE IF NOT EXISTS descriptors (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    descriptor_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id, descriptor_name)
                );

                CREATE TABLE IF NOT EXISTS scores (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    scalar_fitness REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id, stage)
                );

                CREATE TABLE IF NOT EXISTS iteration_metrics (
                    run_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    island_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    candidate_id TEXT,
                    quick_fitness REAL,
                    full_fitness REAL,
                    quick_median_us REAL,
                    full_median_us REAL,
                    island_top_fitness REAL,
                    island_coverage_ratio REAL NOT NULL,
                    island_occupied_bins INTEGER NOT NULL,
                    island_accepted_updates INTEGER NOT NULL,
                    global_best_candidate_id TEXT,
                    global_best_fitness REAL,
                    total_tokens INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, iteration, island_id)
                );

                CREATE INDEX IF NOT EXISTS idx_iteration_metrics_run_iteration
                ON iteration_metrics (run_id, iteration);
                """
            )
            self._conn.commit()

    def start_run(
        self,
        *,
        run_id: str,
        problem_id: str,
        manifest: RunManifest,
        config: dict[str, Any],
    ) -> None:
        self._exec_write(
            """
            INSERT INTO runs (run_id, problem_id, status, created_at, manifest_json, config_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                problem_id,
                "running",
                manifest.created_at.isoformat(),
                to_json(manifest),
                to_json(config),
            ),
        )

    def finalize_run(self, summary: RunSummary) -> None:
        self._exec_write(
            """
            UPDATE runs
            SET status = ?, summary_json = ?
            WHERE run_id = ?
            """,
            ("completed", to_json(summary), summary.run_id),
        )

    def run_exists(self, run_id: str) -> bool:
        row = self._exec_read_one(
            """
            SELECT 1 FROM runs WHERE run_id = ? LIMIT 1
            """,
            (run_id,),
        )
        return row is not None

    def save_candidate(self, candidate: Candidate, state: CandidateState) -> None:
        self._exec_write(
            """
            INSERT INTO candidates (candidate_id, run_id, content_hash, state, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                state = excluded.state,
                payload_json = excluded.payload_json
            """,
            (
                candidate.candidate_id,
                candidate.run_id,
                candidate.content_hash,
                state.value,
                candidate.created_at.isoformat(),
                to_json(candidate),
            ),
        )

    def transition_state(
        self,
        *,
        run_id: str,
        candidate_id: str,
        from_state: CandidateState | None,
        to_state: CandidateState,
        reason: str | None = None,
    ) -> None:
        self._exec_writes(
            [
                (
                    """
                    INSERT INTO state_transitions (run_id, candidate_id, from_state, to_state, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (
                        run_id,
                        candidate_id,
                        from_state.value if from_state else None,
                        to_state.value,
                        reason,
                    ),
                ),
                (
                    """
                    UPDATE candidates SET state = ? WHERE candidate_id = ?
                    """,
                    (to_state.value, candidate_id),
                ),
            ]
        )

    def save_build_result(self, result: BuildResult) -> None:
        self._exec_write(
            """
            INSERT INTO build_results (run_id, candidate_id, created_at, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, candidate_id) DO UPDATE SET
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                result.run_id,
                result.candidate_id,
                result.created_at.isoformat(),
                to_json(result),
            ),
        )

    def save_validation_result(self, result: ValidationResult) -> None:
        self._exec_write(
            """
            INSERT INTO validation_results (run_id, candidate_id, created_at, payload_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, candidate_id) DO UPDATE SET
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                result.run_id,
                result.candidate_id,
                result.created_at.isoformat(),
                to_json(result),
            ),
        )

    def save_benchmark_result(self, result: BenchmarkResult) -> None:
        self._exec_write(
            """
            INSERT INTO benchmark_results (run_id, candidate_id, stage, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, candidate_id, stage) DO UPDATE SET
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                result.run_id,
                result.candidate_id,
                result.stage.value,
                result.created_at.isoformat(),
                to_json(result),
            ),
        )

    def save_descriptor(self, descriptor: Descriptor) -> None:
        self._exec_write(
            """
            INSERT INTO descriptors (run_id, candidate_id, descriptor_name, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, candidate_id, descriptor_name) DO UPDATE SET
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                descriptor.run_id,
                descriptor.candidate_id,
                descriptor.descriptor_name,
                descriptor.created_at.isoformat(),
                to_json(descriptor),
            ),
        )

    def save_score(self, score: ScoreRecord) -> None:
        self._exec_write(
            """
            INSERT INTO scores (run_id, candidate_id, stage, created_at, scalar_fitness, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, candidate_id, stage) DO UPDATE SET
                created_at = excluded.created_at,
                scalar_fitness = excluded.scalar_fitness,
                payload_json = excluded.payload_json
            """,
            (
                score.run_id,
                score.candidate_id,
                score.stage.value,
                score.created_at.isoformat(),
                score.scalar_fitness,
                to_json(score),
            ),
        )

    def save_iteration_metric(self, metric: IterationMetric) -> None:
        self.save_iteration_metrics([metric])

    def save_iteration_metrics(self, metrics: list[IterationMetric]) -> None:
        if not metrics:
            return
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO iteration_metrics (
                    run_id,
                    iteration,
                    island_id,
                    created_at,
                    candidate_id,
                    quick_fitness,
                    full_fitness,
                    quick_median_us,
                    full_median_us,
                    island_top_fitness,
                    island_coverage_ratio,
                    island_occupied_bins,
                    island_accepted_updates,
                    global_best_candidate_id,
                    global_best_fitness,
                    total_tokens,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, iteration, island_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    candidate_id = excluded.candidate_id,
                    quick_fitness = excluded.quick_fitness,
                    full_fitness = excluded.full_fitness,
                    quick_median_us = excluded.quick_median_us,
                    full_median_us = excluded.full_median_us,
                    island_top_fitness = excluded.island_top_fitness,
                    island_coverage_ratio = excluded.island_coverage_ratio,
                    island_occupied_bins = excluded.island_occupied_bins,
                    island_accepted_updates = excluded.island_accepted_updates,
                    global_best_candidate_id = excluded.global_best_candidate_id,
                    global_best_fitness = excluded.global_best_fitness,
                    total_tokens = excluded.total_tokens,
                    payload_json = excluded.payload_json
                """,
                [
                    (
                        metric.run_id,
                        metric.iteration,
                        metric.island_id,
                        metric.created_at.isoformat(),
                        metric.candidate_id,
                        metric.quick_fitness,
                        metric.full_fitness,
                        metric.quick_median_us,
                        metric.full_median_us,
                        metric.island_top_fitness,
                        metric.island_coverage_ratio,
                        metric.island_occupied_bins,
                        metric.island_accepted_updates,
                        metric.global_best_candidate_id,
                        metric.global_best_fitness,
                        metric.total_tokens,
                        to_json(metric),
                    )
                    for metric in metrics
                ],
            )
            self._conn.commit()

    def list_scores_for_stage(self, run_id: str, stage: str) -> list[tuple[str, float]]:
        rows = self._exec_read_all(
            """
            SELECT candidate_id, scalar_fitness
            FROM scores
            WHERE run_id = ? AND stage = ?
            ORDER BY scalar_fitness DESC
            """,
            (run_id, stage),
        )
        return [(str(row[0]), float(row[1])) for row in rows]
