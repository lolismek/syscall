#!/usr/bin/env bash
set -euo pipefail

DB="${1:-.runs/search-medium-20m}/db/runs.sqlite"

if [ ! -f "$DB" ]; then
  echo "Database not found: $DB" >&2
  exit 1
fi

RUN_ID="$(sqlite3 "$DB" "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1;")"
STARTED="$(sqlite3 "$DB" "SELECT created_at FROM runs WHERE run_id = '$RUN_ID';")"
STATUS="$(sqlite3 "$DB" "SELECT status FROM runs WHERE run_id = '$RUN_ID';")"
MODEL="$(sqlite3 "$DB" "SELECT json_extract(config_json, '$.nemotron_model') FROM runs WHERE run_id = '$RUN_ID';" 2>/dev/null || echo "?")"

echo "=== Run: ${RUN_ID:0:8}... | $STATUS | started $STARTED | model: $MODEL ==="
echo

echo "--- Iteration Breakdown ---"
sqlite3 "$DB" "
SELECT json_extract(payload_json, '$.payload.reason') as reason, count(*) as n
FROM iteration_metrics WHERE run_id = '$RUN_ID'
GROUP BY reason ORDER BY n DESC;
"
echo

echo "--- Quick Eval Distribution ---"
sqlite3 "$DB" "
SELECT
  CASE
    WHEN s.scalar_fitness = -1e18 THEN '  FAILED'
    WHEN json_extract(s.payload_json, '$.raw_score.speedup_vs_ref') < 0.5 THEN '  slow (<0.5x)'
    WHEN json_extract(s.payload_json, '$.raw_score.speedup_vs_ref') < 1.0 THEN '  near-baseline (0.5-1x)'
    WHEN json_extract(s.payload_json, '$.raw_score.speedup_vs_ref') < 2.0 THEN '  good (1-2x)'
    WHEN json_extract(s.payload_json, '$.raw_score.speedup_vs_ref') < 4.0 THEN '* GREAT (2-4x)'
    ELSE '** EXCELLENT (>4x)'
  END as bucket, count(*) as n
FROM scores s WHERE s.run_id = '$RUN_ID' AND s.stage = 'quick'
GROUP BY bucket ORDER BY n DESC;
"
echo

echo "--- Full Leaderboard (top 10) ---"
sqlite3 -header "$DB" "
SELECT
  substr(c.candidate_id, 1, 8) as id,
  printf('%.2fx', json_extract(s.payload_json, '$.raw_score.speedup_vs_ref')) as speedup,
  printf('%.0f', json_extract(s.payload_json, '$.raw_score.median_us')) as 'us',
  substr(json_extract(c.payload_json, '$.hypothesis'), 1, 70) as hypothesis
FROM scores s JOIN candidates c ON s.candidate_id = c.candidate_id
WHERE s.run_id = '$RUN_ID' AND s.stage = 'full'
ORDER BY s.scalar_fitness DESC LIMIT 10;
"
echo

echo "--- LLM Stats ---"
TOTAL_CANDIDATES="$(sqlite3 "$DB" "SELECT count(*) FROM candidates WHERE run_id = '$RUN_ID';")"
TOTAL_QUICK="$(sqlite3 "$DB" "SELECT count(*) FROM scores WHERE run_id = '$RUN_ID' AND stage = 'quick';")"
TOTAL_FULL="$(sqlite3 "$DB" "SELECT count(*) FROM scores WHERE run_id = '$RUN_ID' AND stage = 'full';")"
REJECTED="$(sqlite3 "$DB" "SELECT count(*) FROM iteration_metrics WHERE run_id = '$RUN_ID' AND json_extract(payload_json, '$.payload.reason') = 'generator_rejected';")"
echo "  candidates=$TOTAL_CANDIDATES quick=$TOTAL_QUICK full=$TOTAL_FULL rejected=$REJECTED"
