from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class DashboardDataError(RuntimeError):
    pass


@dataclass(slots=True)
class DashboardService:
    workspace: Path
    db_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        self.db_path = self.workspace / "db" / "runs.sqlite"

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise DashboardDataError(f"runs database not found at {self.db_path}")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, problem_id, status, created_at, summary_json
                FROM runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            summary = _json_loads(row["summary_json"])
            out.append(
                {
                    "run_id": str(row["run_id"]),
                    "problem_id": str(row["problem_id"]),
                    "status": str(row["status"]),
                    "created_at": str(row["created_at"]),
                    "summary": summary,
                }
            )
        return out

    def run_overview(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            run_row = conn.execute(
                """
                SELECT run_id, problem_id, status, created_at, manifest_json, config_json, summary_json
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise KeyError(f"unknown run_id: {run_id}")

            states = conn.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM candidates
                WHERE run_id = ?
                GROUP BY state
                ORDER BY state ASC
                """,
                (run_id,),
            ).fetchall()

            quick_best = conn.execute(
                """
                SELECT candidate_id, scalar_fitness, payload_json
                FROM scores
                WHERE run_id = ? AND stage = 'quick'
                ORDER BY scalar_fitness DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            full_best = conn.execute(
                """
                SELECT candidate_id, scalar_fitness, payload_json
                FROM scores
                WHERE run_id = ? AND stage = 'full'
                ORDER BY scalar_fitness DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()

            iter_row = conn.execute(
                """
                SELECT iteration, global_best_candidate_id, global_best_fitness, total_tokens
                FROM iteration_metrics
                WHERE run_id = ?
                ORDER BY iteration DESC, island_id ASC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            max_tokens_row = conn.execute(
                """
                SELECT MAX(total_tokens) AS max_total_tokens
                FROM iteration_metrics
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()

        summary = _json_loads(run_row["summary_json"])
        return {
            "run_id": str(run_row["run_id"]),
            "problem_id": str(run_row["problem_id"]),
            "status": str(run_row["status"]),
            "created_at": str(run_row["created_at"]),
            "manifest": _json_loads(run_row["manifest_json"]),
            "config": _json_loads(run_row["config_json"]),
            "summary": summary,
            "state_counts": {str(row["state"]): int(row["count"]) for row in states},
            "best": {
                "quick": self._score_row(quick_best),
                "full": self._score_row(full_best),
            },
            "latest_iteration": (
                {
                    "iteration": int(iter_row["iteration"]),
                    "global_best_candidate_id": (
                        str(iter_row["global_best_candidate_id"]) if iter_row["global_best_candidate_id"] else None
                    ),
                    "global_best_fitness": (
                        float(iter_row["global_best_fitness"]) if iter_row["global_best_fitness"] is not None else None
                    ),
                    "total_tokens": (
                        int(max_tokens_row["max_total_tokens"])
                        if max_tokens_row is not None and max_tokens_row["max_total_tokens"] is not None
                        else int(iter_row["total_tokens"])
                    ),
                    "total_tokens_raw": int(iter_row["total_tokens"]),
                }
                if iter_row is not None
                else None
            ),
        }

    def leader_source(self, run_id: str, *, stage: str = "full") -> dict[str, Any]:
        stage = stage.lower().strip()
        if stage not in {"quick", "full"}:
            raise ValueError("stage must be quick or full")

        with self._connect() as conn:
            score_row = conn.execute(
                """
                SELECT s.candidate_id, s.scalar_fitness
                FROM scores AS s
                WHERE s.run_id = ? AND s.stage = ?
                ORDER BY s.scalar_fitness DESC
                LIMIT 1
                """,
                (run_id, stage),
            ).fetchone()

            if score_row is None:
                other = "quick" if stage == "full" else "full"
                score_row = conn.execute(
                    """
                    SELECT s.candidate_id, s.scalar_fitness
                    FROM scores AS s
                    WHERE s.run_id = ? AND s.stage = ?
                    ORDER BY s.scalar_fitness DESC
                    LIMIT 1
                    """,
                    (run_id, other),
                ).fetchone()

            if score_row is None:
                return {"candidate_id": None, "fitness": None, "files": []}

            candidate_id = str(score_row["candidate_id"])
            fitness = float(score_row["scalar_fitness"])

            cand_row = conn.execute(
                "SELECT payload_json FROM candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()

        if cand_row is None:
            return {"candidate_id": candidate_id, "fitness": fitness, "files": []}

        payload = _json_loads(cand_row["payload_json"])
        representation = payload.get("representation", {})
        files = representation.get("files", [])

        return {
            "candidate_id": candidate_id,
            "fitness": fitness,
            "stage": stage,
            "files": [{"path": f.get("path", "unknown"), "content": f.get("content", "")} for f in files],
            "hypothesis": payload.get("hypothesis", ""),
            "origin": payload.get("origin", {}),
        }

    def leaderboard(self, run_id: str, *, stage: str = "quick", limit: int = 25) -> list[dict[str, Any]]:
        stage = stage.lower().strip()
        if stage not in {"quick", "full"}:
            raise ValueError("stage must be quick or full")

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.candidate_id, s.scalar_fitness, s.created_at, s.payload_json, c.state
                FROM scores AS s
                LEFT JOIN candidates AS c
                    ON c.candidate_id = s.candidate_id
                WHERE s.run_id = ? AND s.stage = ?
                ORDER BY s.scalar_fitness DESC
                LIMIT ?
                """,
                (run_id, stage, max(1, int(limit))),
            ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_loads(row["payload_json"])
            out.append(
                {
                    "candidate_id": str(row["candidate_id"]),
                    "scalar_fitness": float(row["scalar_fitness"]),
                    "created_at": str(row["created_at"]),
                    "state": str(row["state"]) if row["state"] is not None else None,
                    "raw_score": payload.get("raw_score", payload),
                }
            )
        return out

    def state_snapshot(self, run_id: str, *, transition_limit: int = 200) -> dict[str, Any]:
        with self._connect() as conn:
            counts = conn.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM candidates
                WHERE run_id = ?
                GROUP BY state
                ORDER BY state ASC
                """,
                (run_id,),
            ).fetchall()
            transitions = conn.execute(
                """
                SELECT candidate_id, from_state, to_state, reason, created_at
                FROM state_transitions
                WHERE run_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (run_id, max(1, int(transition_limit))),
            ).fetchall()

        return {
            "run_id": run_id,
            "state_counts": {str(row["state"]): int(row["count"]) for row in counts},
            "transitions": [
                {
                    "candidate_id": str(row["candidate_id"]),
                    "from_state": str(row["from_state"]) if row["from_state"] else None,
                    "to_state": str(row["to_state"]),
                    "reason": str(row["reason"]) if row["reason"] is not None else None,
                    "created_at": str(row["created_at"]),
                }
                for row in transitions
            ],
        }

    def timeseries(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    iteration,
                    island_id,
                    candidate_id,
                    created_at,
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
                FROM iteration_metrics
                WHERE run_id = ?
                ORDER BY iteration ASC, island_id ASC
                """,
                (run_id,),
            ).fetchall()
            benchmark_rows = conn.execute(
                """
                SELECT candidate_id, stage, payload_json
                FROM benchmark_results
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()

        quick_median_by_candidate: dict[str, float] = {}
        full_median_by_candidate: dict[str, float] = {}
        for row in benchmark_rows:
            payload = _json_loads(row["payload_json"])
            status = str(payload.get("status", ""))
            if status != "success":
                continue
            timing = payload.get("timing", {})
            if not isinstance(timing, dict):
                continue
            median = timing.get("median_us")
            if median is None:
                continue
            try:
                median_f = float(median)
            except (TypeError, ValueError):
                continue
            cid = str(row["candidate_id"])
            stage = str(row["stage"])
            if stage == "quick":
                quick_median_by_candidate[cid] = median_f
            elif stage == "full":
                full_median_by_candidate[cid] = median_f

        by_iteration: dict[int, list[sqlite3.Row]] = {}
        island_series: dict[str, list[dict[str, Any]]] = {}

        for row in rows:
            iteration = int(row["iteration"])
            by_iteration.setdefault(iteration, []).append(row)

            island_id = str(row["island_id"])
            island_series.setdefault(island_id, []).append(
                {
                    "iteration": iteration,
                    "top_fitness": (float(row["island_top_fitness"]) if row["island_top_fitness"] is not None else None),
                    "coverage_ratio": float(row["island_coverage_ratio"]),
                    "occupied_bins": int(row["island_occupied_bins"]),
                    "accepted_updates": int(row["island_accepted_updates"]),
                }
            )

        global_series: list[dict[str, Any]] = []
        for iteration in sorted(by_iteration.keys()):
            iter_rows = by_iteration[iteration]
            active = next((row for row in iter_rows if row["candidate_id"] is not None), iter_rows[0])
            global_best_cid = (
                str(active["global_best_candidate_id"]) if active["global_best_candidate_id"] is not None else None
            )
            best_full_median = full_median_by_candidate.get(global_best_cid, None) if global_best_cid else None
            best_quick_median = quick_median_by_candidate.get(global_best_cid, None) if global_best_cid else None
            global_series.append(
                {
                    "iteration": iteration,
                    "created_at": str(active["created_at"]) if active["created_at"] else None,
                    "active_island_id": str(active["island_id"]),
                    "active_candidate_id": str(active["candidate_id"]) if active["candidate_id"] is not None else None,
                    "quick_fitness": (float(active["quick_fitness"]) if active["quick_fitness"] is not None else None),
                    "full_fitness": (float(active["full_fitness"]) if active["full_fitness"] is not None else None),
                    "quick_median_us": (float(active["quick_median_us"]) if active["quick_median_us"] is not None else None),
                    "full_median_us": (float(active["full_median_us"]) if active["full_median_us"] is not None else None),
                    "global_best_candidate_id": global_best_cid,
                    "global_best_fitness": (
                        float(active["global_best_fitness"]) if active["global_best_fitness"] is not None else None
                    ),
                    "global_best_quick_median_us": best_quick_median,
                    "global_best_full_median_us": best_full_median,
                    "global_best_median_us": best_full_median if best_full_median is not None else best_quick_median,
                    "total_tokens_raw": int(active["total_tokens"]),
                    "total_tokens": int(active["total_tokens"]),
                    "event": _json_loads(active["payload_json"]),
                }
            )

        # Parallel workers can flush iteration rows out of order; keep displayed
        # "best so far" and cumulative series monotonic for stable charts.
        running_max_tokens = 0
        running_best_fitness: float | None = None
        running_best_median_us: float | None = None
        running_best_quick_fitness: float | None = None
        running_best_full_fitness: float | None = None
        running_best_quick_median_us: float | None = None
        running_best_full_median_us: float | None = None
        for point in global_series:
            raw = int(point.get("total_tokens_raw", point.get("total_tokens", 0)))
            if raw > running_max_tokens:
                running_max_tokens = raw
            point["total_tokens"] = running_max_tokens

            raw_fitness = point.get("global_best_fitness")
            if isinstance(raw_fitness, (int, float)):
                fitness = float(raw_fitness)
                if running_best_fitness is None or fitness > running_best_fitness:
                    running_best_fitness = fitness
            point["global_best_fitness"] = running_best_fitness

            raw_median = point.get("global_best_median_us")
            if isinstance(raw_median, (int, float)):
                median_us = float(raw_median)
                if median_us > 0 and (running_best_median_us is None or median_us < running_best_median_us):
                    running_best_median_us = median_us
            point["global_best_median_us"] = running_best_median_us

            raw_quick_fitness = point.get("quick_fitness")
            if isinstance(raw_quick_fitness, (int, float)):
                quick_fitness = float(raw_quick_fitness)
                if running_best_quick_fitness is None or quick_fitness > running_best_quick_fitness:
                    running_best_quick_fitness = quick_fitness
            point["best_quick_fitness"] = running_best_quick_fitness

            raw_full_fitness = point.get("full_fitness")
            if isinstance(raw_full_fitness, (int, float)):
                full_fitness = float(raw_full_fitness)
                if running_best_full_fitness is None or full_fitness > running_best_full_fitness:
                    running_best_full_fitness = full_fitness
            point["best_full_fitness"] = running_best_full_fitness

            raw_quick_median = point.get("quick_median_us")
            if isinstance(raw_quick_median, (int, float)):
                quick_median_us = float(raw_quick_median)
                if quick_median_us > 0 and (
                    running_best_quick_median_us is None or quick_median_us < running_best_quick_median_us
                ):
                    running_best_quick_median_us = quick_median_us
            point["best_quick_median_us"] = running_best_quick_median_us

            raw_full_median = point.get("full_median_us")
            if isinstance(raw_full_median, (int, float)):
                full_median_us = float(raw_full_median)
                if full_median_us > 0 and (
                    running_best_full_median_us is None or full_median_us < running_best_full_median_us
                ):
                    running_best_full_median_us = full_median_us
            point["best_full_median_us"] = running_best_full_median_us

            point["best_representative_fitness"] = (
                running_best_full_fitness if running_best_full_fitness is not None else running_best_quick_fitness
            )
            point["best_representative_median_us"] = (
                running_best_full_median_us
                if running_best_full_median_us is not None
                else running_best_quick_median_us
            )

        return {
            "run_id": run_id,
            "global": global_series,
            "islands": island_series,
        }

    @staticmethod
    def _score_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = _json_loads(row["payload_json"])
        raw_score = payload.get("raw_score", payload)
        return {
            "candidate_id": str(row["candidate_id"]),
            "scalar_fitness": float(row["scalar_fitness"]),
            "raw_score": raw_score,
        }


class DashboardHTTPServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, service: DashboardService) -> None:
        self.service = service
        self._base_url = f"http://{host}:{port}"
        super().__init__((host, port), _DashboardHandler)

    @property
    def base_url(self) -> str:
        return self._base_url


class _DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    _DIST_DIR: Path | None = None

    @classmethod
    def _get_dist_dir(cls) -> Path | None:
        if cls._DIST_DIR is not None:
            return cls._DIST_DIR if cls._DIST_DIR.is_dir() else None
        candidate = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"
        cls._DIST_DIR = candidate
        return candidate if candidate.is_dir() else None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "status": "ready",
                    "workspace": str(self.server.service.workspace),
                },
            )
            return

        if path.startswith("/api/"):
            try:
                payload = self._handle_api(parsed)
                self._send_json(HTTPStatus.OK, payload)
            except KeyError as exc:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except DashboardDataError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return

        dist_dir = self._get_dist_dir()
        if dist_dir is not None:
            safe = path.lstrip("/") or "index.html"
            file_path = (dist_dir / safe).resolve()
            if file_path.is_file() and str(file_path).startswith(str(dist_dir)):
                self._send_static(file_path)
                return
            index = dist_dir / "index.html"
            if index.is_file():
                self._send_static(index)
                return

        self._send_html(HTTPStatus.OK, _dashboard_html())

    def _handle_api(self, parsed) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/runs":
            limit = int(query.get("limit", ["50"])[0])
            return {"ok": True, "runs": self.server.service.list_runs(limit=limit)}

        parts = path.strip("/").split("/")
        # /api/runs/<run_id>/<resource>
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "runs":
            raise KeyError("not found")

        run_id = parts[2]
        resource = parts[3]
        if resource == "overview":
            return {"ok": True, "overview": self.server.service.run_overview(run_id)}
        if resource == "timeseries":
            return {"ok": True, "timeseries": self.server.service.timeseries(run_id)}
        if resource == "states":
            limit = int(query.get("limit", ["200"])[0])
            return {"ok": True, "states": self.server.service.state_snapshot(run_id, transition_limit=limit)}
        if resource == "leaderboard":
            stage = str(query.get("stage", ["quick"])[0])
            limit = int(query.get("limit", ["25"])[0])
            return {
                "ok": True,
                "stage": stage,
                "rows": self.server.service.leaderboard(run_id, stage=stage, limit=limit),
            }
        if resource == "leader-source":
            stage = str(query.get("stage", ["full"])[0])
            return {"ok": True, "leader": self.server.service.leader_source(run_id, stage=stage)}

        raise KeyError("not found")

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, file_path: Path) -> None:
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type is None:
            mime_type = "application/octet-stream"
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        if file_path.suffix in {".js", ".css", ".woff2", ".svg"}:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>KernelSwarm Dashboard</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --ink: #122033;
      --muted: #5f6f85;
      --line: #dbe3ef;
      --accent: #0a7d5a;
      --warn: #c6461a;
      --blue: #1f6feb;
      --orange: #d07a00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at 20% 0%, #fff 0%, #f1f5fb 35%, #ecf1f9 100%);
    }
    header {
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(120deg, #ffffff, #edf3ff);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .title { font-weight: 700; letter-spacing: .2px; }
    .muted { color: var(--muted); font-size: 12px; }
    .container {
      padding: 14px;
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 12px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px;
      box-shadow: 0 1px 2px rgba(16,24,40,.03);
    }
    .span-4 { grid-column: span 4; }
    .span-6 { grid-column: span 6; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .kpis { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 8px; }
    .kpi { border: 1px solid var(--line); border-radius: 8px; padding: 8px; background: #fbfdff; }
    .kpi .v { font-weight: 700; font-size: 15px; }
    .kpi .l { font-size: 11px; color: var(--muted); }
    canvas { width: 100%; height: 200px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 6px 4px; }
    select, button { padding: 6px 8px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
    .islands { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .island-card { border: 1px solid var(--line); border-radius: 10px; padding: 8px; background: #fcfeff; }
    .pill { display: inline-block; font-size: 11px; color: #fff; background: var(--blue); border-radius: 999px; padding: 2px 8px; }
    @media (max-width: 1100px) {
      .span-4,.span-6,.span-8,.span-12 { grid-column: span 12; }
      .kpis { grid-template-columns: repeat(2, minmax(0,1fr)); }
      .islands { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<header>
  <div class=\"row\">
    <div class=\"title\">KernelSwarm Live Dashboard</div>
    <span class=\"pill\" id=\"runStatus\">no run selected</span>
    <span class=\"muted\" id=\"lastRefresh\"></span>
  </div>
  <div class=\"row\" style=\"margin-top:8px\">
    <label for=\"runSelect\" class=\"muted\">Run</label>
    <select id=\"runSelect\"></select>
    <button id=\"refreshBtn\">Refresh</button>
  </div>
</header>

<div class=\"container\">
  <div class=\"card span-12\">
    <div class=\"kpis\" id=\"kpis\"></div>
  </div>

  <div class=\"card span-6\">
    <div class=\"row\"><strong>Best Fitness Over Iterations</strong></div>
    <canvas id=\"fitnessChart\" width=\"900\" height=\"220\"></canvas>
  </div>

  <div class=\"card span-6\">
    <div class=\"row\"><strong>Best Median Latency (us) Over Iterations</strong></div>
    <canvas id=\"latencyChart\" width=\"900\" height=\"220\"></canvas>
  </div>

  <div class=\"card span-6\">
    <div class=\"row\"><strong>Total Tokens Over Iterations</strong></div>
    <canvas id=\"tokenChart\" width=\"900\" height=\"220\"></canvas>
  </div>

  <div class=\"card span-6\">
    <div class=\"row\"><strong>Candidate States</strong></div>
    <table><thead><tr><th>State</th><th>Count</th></tr></thead><tbody id=\"stateRows\"></tbody></table>
  </div>

  <div class=\"card span-12\">
    <div class=\"row\"><strong>Island Evolution State</strong></div>
    <div class=\"islands\" id=\"islands\"></div>
  </div>

  <div class=\"card span-6\">
    <div class=\"row\"><strong>Quick Leaderboard</strong></div>
    <table>
      <thead><tr><th>Candidate</th><th>Fitness</th><th>Median us</th><th>State</th></tr></thead>
      <tbody id=\"quickRows\"></tbody>
    </table>
  </div>

  <div class=\"card span-6\">
    <div class=\"row\"><strong>Full Leaderboard</strong></div>
    <table>
      <thead><tr><th>Candidate</th><th>Fitness</th><th>Median us</th><th>State</th></tr></thead>
      <tbody id=\"fullRows\"></tbody>
    </table>
  </div>
</div>

<script>
const runSelect = document.getElementById('runSelect');
const refreshBtn = document.getElementById('refreshBtn');
const runStatus = document.getElementById('runStatus');
const lastRefresh = document.getElementById('lastRefresh');
const _queryParams = new URLSearchParams(window.location.search || '');
const preferredRunId = _queryParams.get('run_id');
let activeRun = preferredRunId || null;

async function fetchJson(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

function shortId(s) { return s ? String(s).slice(0, 8) : 'n/a'; }
function fmt(n, d=3) { return (n === null || n === undefined || Number.isNaN(Number(n))) ? 'n/a' : Number(n).toFixed(d); }

function chartReferencesForProblem(problemId) {
  const id = String(problemId || '').toLowerCase();
  const latencyByProblem = {
    kernelbench_v1: { baseline: 30000, good: 10000, sota_est: 5000 },
    vector_add_v1: { baseline: 2000, good: 500, sota_est: 120 },
    reduction_v1: { baseline: 3000, good: 1000, sota_est: 250 },
    stencil2d_v1: { baseline: 2500, good: 900, sota_est: 280 },
  };
  const ref = latencyByProblem[id];
  if (!ref) return null;

  const lines = [
    { key: 'baseline', label: 'baseline', color: '#7b8798', value: Number(ref.baseline) },
    { key: 'good', label: 'good', color: '#d07a00', value: Number(ref.good) },
    { key: 'sota_est', label: 'sota est', color: '#0a7d5a', value: Number(ref.sota_est) },
  ];
  const valid = lines.filter((x) => Number.isFinite(x.value) && x.value > 0);
  if (!valid.length) return null;

  const yMinLatency = Math.max(1e-9, Math.min(...valid.map((x) => x.value)) * 0.7);
  const yMaxLatency = Math.max(yMinLatency + 1e-9, Math.max(...valid.map((x) => x.value)) * 1.3);
  const latencyLines = valid.map((x) => ({ ...x }));
  const fitnessLines = valid.map((x) => ({ ...x, value: 1_000_000.0 / x.value }));

  return {
    latency: {
      yMin: yMinLatency,
      yMax: yMaxLatency,
      references: latencyLines,
    },
    fitness: {
      yMin: 1_000_000.0 / yMaxLatency,
      yMax: 1_000_000.0 / yMinLatency,
      references: fitnessLines,
    },
  };
}

function downsampleSeries(series, maxPoints) {
  if (!Number.isFinite(maxPoints) || maxPoints <= 0 || series.length <= maxPoints) return series;
  const out = [];
  const last = series.length - 1;
  const denom = Math.max(1, maxPoints - 1);
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.round((i * last) / denom);
    out.push(series[idx]);
  }
  return out;
}

function drawLine(canvasId, points, valueKey, color, opts={}) {
  const c = document.getElementById(canvasId);
  if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.width, h = c.height;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle = '#fff'; ctx.fillRect(0,0,w,h);

  const xKey = opts.xKey || 'iteration';
  const maxPoints = Number(opts.maxPoints || 600);
  const seriesRaw = (points || []).map((p, i) => {
    const y = Number(p[valueKey]);
    if (!Number.isFinite(y)) return null;
    const x = Number(p[xKey]);
    return { x: Number.isFinite(x) ? x : i, y };
  }).filter(Boolean);
  const series = downsampleSeries(seriesRaw, maxPoints);

  if (!series.length) {
    ctx.fillStyle = '#6a7a90';
    ctx.font = '12px sans-serif';
    ctx.fillText('no data yet', 12, 20);
    return;
  }

  const vals = series.map(p => p.y);
  const xs = series.map(p => p.x);
  const dataMin = Math.min(...vals), dataMax = Math.max(...vals);
  const fixedYMin = Number(opts.yMin);
  const fixedYMax = Number(opts.yMax);
  const useFixedY = Number.isFinite(fixedYMin) && Number.isFinite(fixedYMax) && fixedYMax > fixedYMin;
  const min = useFixedY ? fixedYMin : dataMin;
  const max = useFixedY ? fixedYMax : dataMax;
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const pad = 28;
  const plotW = w - pad * 2;
  const plotH = h - pad * 2;
  const xSpan = Math.max(1e-9, maxX - minX);
  const ySpan = Math.max(1e-9, max - min);

  ctx.strokeStyle = '#d9e2ef';
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    const y = pad + (plotH * i / 4);
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w-pad, y); ctx.stroke();
  }

  const references = Array.isArray(opts.references) ? opts.references : [];
  if (references.length) {
    ctx.save();
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    references.forEach((ref) => {
      const rv = Number(ref?.value);
      if (!Number.isFinite(rv)) return;
      if (rv < min || rv > max) return;
      const y = pad + plotH * (1 - ((rv - min) / ySpan));
      const refColor = ref?.color || '#7b8798';
      ctx.strokeStyle = refColor;
      ctx.beginPath();
      ctx.moveTo(pad, y);
      ctx.lineTo(w - pad, y);
      ctx.stroke();
      ctx.fillStyle = refColor;
      ctx.font = '10px sans-serif';
      const label = `${ref?.label || 'ref'} ${fmt(rv, 1)}`;
      ctx.fillText(label, w - 160, Math.max(10, y - 4));
    });
    ctx.restore();
  }

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((pt, i) => {
    const x = pad + plotW * ((pt.x - minX) / xSpan);
    const y = pad + plotH * (1 - ((pt.y - min) / ySpan));
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = '#33465f';
  ctx.font = '11px sans-serif';
  ctx.fillText('min ' + fmt(min, 2), 8, h - 8);
  ctx.fillText('max ' + fmt(max, 2), 8, 14);
  ctx.fillText(`x ${fmt(minX, 0)}-${fmt(maxX, 0)}`, w - 120, h - 8);
}

function renderKpis(overview, timeseries) {
  const global = timeseries.global || [];
  const latest = global.length ? global[global.length - 1] : null;
  const cards = [
    { l: 'Run', v: shortId(overview.run_id) },
    { l: 'Problem', v: overview.problem_id || 'n/a' },
    { l: 'Best Full Fitness', v: fmt(overview.best?.full?.scalar_fitness ?? null, 4) },
    { l: 'Best Full Median us', v: fmt(latest?.best_full_median_us ?? null, 2) },
    { l: 'Best Quick Fitness', v: fmt(overview.best?.quick?.scalar_fitness ?? null, 4) },
    { l: 'Best Quick Median us', v: fmt(latest?.best_quick_median_us ?? null, 2) },
    { l: 'Latest Iteration', v: latest ? String(latest.iteration) : 'n/a' },
    { l: 'Total Tokens', v: latest ? String(latest.total_tokens) : '0' },
  ];
  document.getElementById('kpis').innerHTML = cards.map(c => `<div class=\"kpi\"><div class=\"v\">${c.v}</div><div class=\"l\">${c.l}</div></div>`).join('');
}

function renderStates(states) {
  const rows = Object.entries(states.state_counts || {}).sort((a,b) => a[0].localeCompare(b[0]));
  document.getElementById('stateRows').innerHTML = rows.map(([k,v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');
}

function renderLeaderboard(elId, rows) {
  const body = document.getElementById(elId);
  body.innerHTML = (rows || []).map((r) => {
    const raw = r.raw_score || {};
    const med = raw.median_us;
    return `<tr><td>${shortId(r.candidate_id)}</td><td>${fmt(r.scalar_fitness,4)}</td><td>${fmt(med,2)}</td><td>${r.state || 'n/a'}</td></tr>`;
  }).join('');
}

function renderIslands(ts) {
  const container = document.getElementById('islands');
  const islands = ts.islands || {};
  const ids = Object.keys(islands).sort();
  const latestByIsland = {};
  ids.forEach((id) => {
    const pts = islands[id] || [];
    latestByIsland[id] = pts.length ? pts[pts.length - 1] : null;
  });
  container.innerHTML = ids.map((id) => `
    <div class=\"island-card\">
      <div class=\"row\" style=\"justify-content:space-between\"><strong>${id}</strong><span class=\"muted\">top fitness</span></div>
      <div class=\"muted\" style=\"margin:2px 0 6px 0\">coverage=${fmt(latestByIsland[id]?.coverage_ratio ?? null, 4)} bins=${latestByIsland[id]?.occupied_bins ?? 'n/a'}</div>
      <canvas id=\"island-${id}\" width=\"600\" height=\"180\"></canvas>
    </div>
  `).join('');
  ids.forEach((id) => {
    const pts = islands[id] || [];
    drawLine(`island-${id}`, pts, 'top_fitness', '#0a7d5a');
  });
}

async function loadRuns() {
  const data = await fetchJson('/api/runs?limit=100');
  const runs = data.runs || [];
  runSelect.innerHTML = runs.map((r) => `<option value=\"${r.run_id}\">${shortId(r.run_id)} · ${r.problem_id} · ${r.status}</option>`).join('');
  const hasActive = !!activeRun && runs.some((r) => r.run_id === activeRun);
  if (hasActive) {
    runSelect.value = activeRun;
    return;
  }
  if (runs.length) {
    activeRun = runs[0].run_id;
    runSelect.value = activeRun;
  }
}

async function refresh() {
  if (!activeRun) return;
  const [overviewRes, tsRes, statesRes, quickRes, fullRes] = await Promise.all([
    fetchJson(`/api/runs/${activeRun}/overview`),
    fetchJson(`/api/runs/${activeRun}/timeseries`),
    fetchJson(`/api/runs/${activeRun}/states`),
    fetchJson(`/api/runs/${activeRun}/leaderboard?stage=quick&limit=12`),
    fetchJson(`/api/runs/${activeRun}/leaderboard?stage=full&limit=12`),
  ]);

  const overview = overviewRes.overview;
  const ts = tsRes.timeseries;
  renderKpis(overview, ts);
  renderStates(statesRes.states);
  renderLeaderboard('quickRows', quickRes.rows || []);
  renderLeaderboard('fullRows', fullRes.rows || []);
  renderIslands(ts);

  const global = ts.global || [];
  const chartRefs = chartReferencesForProblem(overview.problem_id);
  drawLine('fitnessChart', global, 'best_representative_fitness', '#0a7d5a', {
    xKey: 'iteration',
    maxPoints: 900,
    yMin: chartRefs?.fitness?.yMin,
    yMax: chartRefs?.fitness?.yMax,
    references: chartRefs?.fitness?.references || [],
  });
  drawLine('latencyChart', global, 'best_representative_median_us', '#c6461a', {
    xKey: 'iteration',
    maxPoints: 900,
    yMin: chartRefs?.latency?.yMin,
    yMax: chartRefs?.latency?.yMax,
    references: chartRefs?.latency?.references || [],
  });
  drawLine('tokenChart', global, 'total_tokens', '#1f6feb', { xKey: 'iteration', maxPoints: 900 });

  runStatus.textContent = `${overview.status} · ${overview.problem_id}`;
  lastRefresh.textContent = `refreshed ${new Date().toLocaleTimeString()}`;
}

runSelect.addEventListener('change', () => { activeRun = runSelect.value; refresh(); });
refreshBtn.addEventListener('click', () => refresh());

(async () => {
  await loadRuns();
  await refresh();
  setInterval(refresh, 2000);
})();
</script>
</body>
</html>
"""


class DashboardServer:
    def __init__(self, host: str, port: int, service: DashboardService) -> None:
        self._server = DashboardHTTPServer(host, port, service)
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return self._server.base_url

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


__all__ = [
    "DashboardDataError",
    "DashboardService",
    "DashboardServer",
]
