from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .models import (
    BenchmarkResult,
    BuildResult,
    Candidate,
    CandidateState,
    Descriptor,
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
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
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
        self._conn.execute(
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
        self._conn.commit()

    def finalize_run(self, summary: RunSummary) -> None:
        self._conn.execute(
            """
            UPDATE runs
            SET status = ?, summary_json = ?
            WHERE run_id = ?
            """,
            ("completed", to_json(summary), summary.run_id),
        )
        self._conn.commit()

    def save_candidate(self, candidate: Candidate, state: CandidateState) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def transition_state(
        self,
        *,
        run_id: str,
        candidate_id: str,
        from_state: CandidateState | None,
        to_state: CandidateState,
        reason: str | None = None,
    ) -> None:
        self._conn.execute(
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
        )
        self._conn.execute(
            """
            UPDATE candidates SET state = ? WHERE candidate_id = ?
            """,
            (to_state.value, candidate_id),
        )
        self._conn.commit()

    def save_build_result(self, result: BuildResult) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def save_validation_result(self, result: ValidationResult) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def save_benchmark_result(self, result: BenchmarkResult) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def save_descriptor(self, descriptor: Descriptor) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def save_score(self, score: ScoreRecord) -> None:
        self._conn.execute(
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
        self._conn.commit()

    def list_scores_for_stage(self, run_id: str, stage: str) -> list[tuple[str, float]]:
        cursor = self._conn.execute(
            """
            SELECT candidate_id, scalar_fitness
            FROM scores
            WHERE run_id = ? AND stage = ?
            ORDER BY scalar_fitness DESC
            """,
            (run_id, stage),
        )
        return [(row[0], float(row[1])) for row in cursor.fetchall()]
