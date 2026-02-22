#!/usr/bin/env python3
"""Export evolution run data from SQLite databases into a static JSON file for the market dashboard.

Mirrors the queries from evolution/src/kernelswarm/dashboard.py DashboardService:
  - run_overview()  → KPI metrics, state counts, best scores
  - timeseries()    → global series (fitness/latency/tokens) + island series
  - leaderboard()   → top candidates by stage (quick/full)
"""

import json
import sqlite3
import sys
from pathlib import Path

EVOLUTION_DIR = Path(__file__).resolve().parent.parent.parent / "evolution"

RUNS = [
    {
        "db_path": EVOLUTION_DIR / ".runs/search-selective-scan/db/runs.sqlite",
        "run_id_prefix": "4d172e54",
        "name": "Mamba Selective Scan",
        "description": "LLM-guided optimization of the Mamba selective scan CUDA kernel using evolutionary search with MAP-Elites.",
    },
    {
        "db_path": EVOLUTION_DIR / ".runs/search-medium-20m/db/runs.sqlite",
        "run_id_prefix": "d1ecb653",
        "name": "LayerNorm",
        "description": "Evolutionary kernel optimization for LayerNorm — exploring vectorized, tiled, and fused variants via LLM-guided mutation.",
    },
]

MAX_TIMESERIES_POINTS = 300


def _json_loads(s):
    if s is None:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def connect_readonly(db_path: str) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def find_run_id(conn: sqlite3.Connection, prefix: str) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM runs WHERE run_id LIKE ? LIMIT 1", (prefix + "%",)
    ).fetchone()
    return row["run_id"] if row else None


def get_overview(conn: sqlite3.Connection, run_id: str) -> dict:
    """Mirrors DashboardService.run_overview()."""
    run_row = conn.execute(
        "SELECT run_id, problem_id, status, created_at FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    states = conn.execute(
        "SELECT state, COUNT(*) AS count FROM candidates WHERE run_id = ? GROUP BY state ORDER BY state",
        (run_id,),
    ).fetchall()

    quick_best = conn.execute(
        "SELECT candidate_id, scalar_fitness, payload_json FROM scores WHERE run_id = ? AND stage = 'quick' ORDER BY scalar_fitness DESC LIMIT 1",
        (run_id,),
    ).fetchone()

    full_best = conn.execute(
        "SELECT candidate_id, scalar_fitness, payload_json FROM scores WHERE run_id = ? AND stage = 'full' ORDER BY scalar_fitness DESC LIMIT 1",
        (run_id,),
    ).fetchone()

    iter_row = conn.execute(
        "SELECT iteration, global_best_candidate_id, global_best_fitness, total_tokens FROM iteration_metrics WHERE run_id = ? ORDER BY iteration DESC, island_id ASC LIMIT 1",
        (run_id,),
    ).fetchone()

    max_tokens_row = conn.execute(
        "SELECT MAX(total_tokens) AS max_total_tokens FROM iteration_metrics WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    def score_row(row):
        if row is None:
            return None
        payload = _json_loads(row["payload_json"]) or {}
        return {
            "candidate_id": str(row["candidate_id"]),
            "scalar_fitness": float(row["scalar_fitness"]),
            "raw_score": payload.get("raw_score", payload),
        }

    total_tokens = 0
    latest_iteration = 0
    if iter_row:
        latest_iteration = int(iter_row["iteration"])
        total_tokens = int(
            max_tokens_row["max_total_tokens"]
            if max_tokens_row and max_tokens_row["max_total_tokens"]
            else iter_row["total_tokens"]
        )

    candidate_count = conn.execute(
        "SELECT COUNT(*) AS c FROM candidates WHERE run_id = ?", (run_id,)
    ).fetchone()["c"]

    return {
        "run_id": str(run_row["run_id"]),
        "problem_id": str(run_row["problem_id"]),
        "status": str(run_row["status"]),
        "created_at": str(run_row["created_at"]),
        "state_counts": {str(row["state"]): int(row["count"]) for row in states},
        "best": {
            "quick": score_row(quick_best),
            "full": score_row(full_best),
        },
        "latest_iteration": latest_iteration,
        "total_tokens": total_tokens,
        "candidate_count": candidate_count,
    }


def get_timeseries(conn: sqlite3.Connection, run_id: str) -> dict:
    """Mirrors DashboardService.timeseries() — produces global + island series
    with monotonic running-best computations."""
    rows = conn.execute(
        """
        SELECT iteration, island_id, candidate_id, created_at,
               quick_fitness, full_fitness, quick_median_us, full_median_us,
               island_top_fitness, island_coverage_ratio, island_occupied_bins,
               island_accepted_updates, global_best_candidate_id, global_best_fitness,
               total_tokens, payload_json
        FROM iteration_metrics
        WHERE run_id = ?
        ORDER BY iteration ASC, island_id ASC
        """,
        (run_id,),
    ).fetchall()

    # Build benchmark median lookup (same as dashboard.py)
    benchmark_rows = conn.execute(
        "SELECT candidate_id, stage, payload_json FROM benchmark_results WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    quick_median_by_candidate: dict[str, float] = {}
    full_median_by_candidate: dict[str, float] = {}
    for row in benchmark_rows:
        payload = _json_loads(row["payload_json"]) or {}
        if str(payload.get("status", "")) != "success":
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

    # Group by iteration
    by_iteration: dict[int, list] = {}
    island_series: dict[str, list] = {}

    for row in rows:
        iteration = int(row["iteration"])
        by_iteration.setdefault(iteration, []).append(row)

        island_id = str(row["island_id"])
        island_series.setdefault(island_id, []).append({
            "iteration": iteration,
            "top_fitness": float(row["island_top_fitness"]) if row["island_top_fitness"] is not None else None,
            "coverage_ratio": float(row["island_coverage_ratio"]),
            "occupied_bins": int(row["island_occupied_bins"]),
            "accepted_updates": int(row["island_accepted_updates"]),
        })

    # Build global series
    global_series: list[dict] = []
    for iteration in sorted(by_iteration.keys()):
        iter_rows = by_iteration[iteration]
        active = next((r for r in iter_rows if r["candidate_id"] is not None), iter_rows[0])
        global_best_cid = str(active["global_best_candidate_id"]) if active["global_best_candidate_id"] else None
        best_full_median = full_median_by_candidate.get(global_best_cid) if global_best_cid else None
        best_quick_median = quick_median_by_candidate.get(global_best_cid) if global_best_cid else None
        global_series.append({
            "iteration": iteration,
            "quick_fitness": float(active["quick_fitness"]) if active["quick_fitness"] is not None else None,
            "full_fitness": float(active["full_fitness"]) if active["full_fitness"] is not None else None,
            "quick_median_us": float(active["quick_median_us"]) if active["quick_median_us"] is not None else None,
            "full_median_us": float(active["full_median_us"]) if active["full_median_us"] is not None else None,
            "global_best_fitness": float(active["global_best_fitness"]) if active["global_best_fitness"] is not None else None,
            "global_best_median_us": best_full_median if best_full_median is not None else best_quick_median,
            "total_tokens_raw": int(active["total_tokens"]),
            "total_tokens": int(active["total_tokens"]),
        })

    # Monotonic running-best pass (mirrors dashboard.py lines 410-476)
    running_max_tokens = 0
    running_best_fitness = None
    running_best_median_us = None
    running_best_quick_fitness = None
    running_best_full_fitness = None
    running_best_quick_median_us = None
    running_best_full_median_us = None

    for point in global_series:
        raw = int(point.get("total_tokens_raw", point.get("total_tokens", 0)))
        if raw > running_max_tokens:
            running_max_tokens = raw
        point["total_tokens"] = running_max_tokens

        raw_fitness = point.get("global_best_fitness")
        if isinstance(raw_fitness, (int, float)):
            if running_best_fitness is None or raw_fitness > running_best_fitness:
                running_best_fitness = raw_fitness
        point["global_best_fitness"] = running_best_fitness

        raw_median = point.get("global_best_median_us")
        if isinstance(raw_median, (int, float)) and raw_median > 0:
            if running_best_median_us is None or raw_median < running_best_median_us:
                running_best_median_us = raw_median
        point["global_best_median_us"] = running_best_median_us

        raw_qf = point.get("quick_fitness")
        if isinstance(raw_qf, (int, float)):
            if running_best_quick_fitness is None or raw_qf > running_best_quick_fitness:
                running_best_quick_fitness = raw_qf
        point["best_quick_fitness"] = running_best_quick_fitness

        raw_ff = point.get("full_fitness")
        if isinstance(raw_ff, (int, float)):
            if running_best_full_fitness is None or raw_ff > running_best_full_fitness:
                running_best_full_fitness = raw_ff
        point["best_full_fitness"] = running_best_full_fitness

        raw_qm = point.get("quick_median_us")
        if isinstance(raw_qm, (int, float)) and raw_qm > 0:
            if running_best_quick_median_us is None or raw_qm < running_best_quick_median_us:
                running_best_quick_median_us = raw_qm
        point["best_quick_median_us"] = running_best_quick_median_us

        raw_fm = point.get("full_median_us")
        if isinstance(raw_fm, (int, float)) and raw_fm > 0:
            if running_best_full_median_us is None or raw_fm < running_best_full_median_us:
                running_best_full_median_us = raw_fm
        point["best_full_median_us"] = running_best_full_median_us

        point["best_representative_fitness"] = (
            running_best_full_fitness if running_best_full_fitness is not None else running_best_quick_fitness
        )
        point["best_representative_median_us"] = (
            running_best_full_median_us if running_best_full_median_us is not None else running_best_quick_median_us
        )

    # Downsample global series
    global_series = downsample(global_series, MAX_TIMESERIES_POINTS)

    return {
        "global": global_series,
        "islands": island_series,
    }


def downsample(series: list[dict], max_points: int) -> list[dict]:
    """Simple stride downsampling. Keeps first and last points."""
    if len(series) <= max_points:
        return series
    step = len(series) / max_points
    result = []
    for i in range(max_points):
        idx = int(i * step)
        result.append(series[idx])
    if result[-1] is not series[-1]:
        result[-1] = series[-1]
    return result


def get_leaderboard(conn: sqlite3.Connection, run_id: str, stage: str, limit: int = 12) -> list[dict]:
    """Mirrors DashboardService.leaderboard(). Columns: candidate_id, scalar_fitness, median_us, state."""
    rows = conn.execute(
        """
        SELECT s.candidate_id, s.scalar_fitness, s.created_at, s.payload_json, c.state
        FROM scores AS s
        LEFT JOIN candidates AS c ON c.candidate_id = s.candidate_id
        WHERE s.run_id = ? AND s.stage = ?
        ORDER BY s.scalar_fitness DESC
        LIMIT ?
        """,
        (run_id, stage, limit),
    ).fetchall()

    out = []
    for row in rows:
        payload = _json_loads(row["payload_json"]) or {}
        raw = payload.get("raw_score", payload)
        median_us = None
        if isinstance(raw, dict):
            median_us = raw.get("median_us")
        out.append({
            "candidate_id": str(row["candidate_id"]),
            "scalar_fitness": float(row["scalar_fitness"]),
            "median_us": float(median_us) if median_us is not None else None,
            "state": str(row["state"]) if row["state"] is not None else None,
        })
    return out


def export_run(run_config: dict) -> dict | None:
    db_path = run_config["db_path"]
    if not db_path.exists():
        print(f"  SKIP: {db_path} not found", file=sys.stderr)
        return None

    conn = connect_readonly(str(db_path))
    try:
        run_id = find_run_id(conn, run_config["run_id_prefix"])
        if not run_id:
            print(f"  SKIP: no run matching prefix {run_config['run_id_prefix']}", file=sys.stderr)
            return None

        print(f"  Exporting run {run_id[:12]}...")

        overview = get_overview(conn, run_id)
        ts = get_timeseries(conn, run_id)
        leaderboard_quick = get_leaderboard(conn, run_id, "quick", 12)
        leaderboard_full = get_leaderboard(conn, run_id, "full", 12)

        # Extract best median from the last timeseries point
        global_series = ts["global"]
        latest = global_series[-1] if global_series else {}

        return {
            "id": run_id,
            "name": run_config["name"],
            "description": run_config["description"],
            "problem_id": overview["problem_id"],
            "status": overview["status"],
            "created_at": overview["created_at"],
            "candidate_count": overview["candidate_count"],
            "latest_iteration": overview["latest_iteration"],
            "total_tokens": overview["total_tokens"],
            "state_counts": overview["state_counts"],
            "best": overview["best"],
            # Derived from latest timeseries point (same as dashboard KPIs)
            "best_full_median_us": latest.get("best_full_median_us"),
            "best_quick_median_us": latest.get("best_quick_median_us"),
            # Timeseries for charts
            "timeseries": ts,
            # Leaderboards
            "leaderboard_quick": leaderboard_quick,
            "leaderboard_full": leaderboard_full,
        }
    finally:
        conn.close()


def main():
    out_path = Path(__file__).resolve().parent.parent / "data" / "evolution-runs.json"
    print(f"Exporting evolution runs to {out_path}")

    runs = []
    for rc in RUNS:
        print(f"Processing: {rc['name']}")
        result = export_run(rc)
        if result:
            runs.append(result)
            print(f"  OK: {result['candidate_count']} candidates, {result['latest_iteration']} iterations, "
                  f"ts={len(result['timeseries']['global'])} pts, "
                  f"islands={len(result['timeseries']['islands'])} islands")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"runs": runs}, f, indent=2)

    print(f"\nDone. {len(runs)} run(s) exported to {out_path}")


if __name__ == "__main__":
    main()
