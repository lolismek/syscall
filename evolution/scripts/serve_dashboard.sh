#!/usr/bin/env bash
set -euo pipefail

DEFAULT_WORKSPACE=".runs/search-medium-20m"
if [ ! -d "$DEFAULT_WORKSPACE" ]; then
  DEFAULT_WORKSPACE=".runs"
fi

TARGET="${1:-${KERNELSWARM_DASHBOARD_TARGET:-}}"
SECOND_ARG="${2:-}"
WORKSPACE="${KERNELSWARM_DASHBOARD_WORKSPACE:-$DEFAULT_WORKSPACE}"
RUN_ID="${KERNELSWARM_DASHBOARD_RUN_ID:-}"
HOST="${KERNELSWARM_DASHBOARD_HOST:-127.0.0.1}"
PORT="${KERNELSWARM_DASHBOARD_PORT:-8090}"
DASHBOARD_DRY_RUN="${KERNELSWARM_DASHBOARD_DRY_RUN:-0}"

is_uuid() {
  [[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

resolve_workspace_for_run_id() {
  local run_id="$1"
  local db
  while IFS= read -r db; do
    if sqlite3 "$db" "SELECT 1 FROM runs WHERE run_id = '$run_id' LIMIT 1;" 2>/dev/null | grep -q "1"; then
      dirname "$(dirname "$db")"
      return 0
    fi
  done < <(find .runs -type f -path '*/db/runs.sqlite' 2>/dev/null | sort)
  return 1
}

if [ -n "$TARGET" ]; then
  if [ -d "$TARGET" ]; then
    WORKSPACE="$TARGET"
    if [ -n "$SECOND_ARG" ]; then
      RUN_ID="$SECOND_ARG"
    fi
  elif is_uuid "$TARGET"; then
    RUN_ID="$TARGET"
    if resolved="$(resolve_workspace_for_run_id "$RUN_ID")"; then
      WORKSPACE="$resolved"
    else
      echo "Run ID not found under .runs: $RUN_ID" >&2
      exit 1
    fi
  else
    echo "Unknown dashboard target: $TARGET" >&2
    echo "Pass either a workspace path or a run_id UUID." >&2
    exit 1
  fi
fi

if [ -n "$RUN_ID" ] && ! is_uuid "$RUN_ID"; then
  echo "Invalid run_id format: $RUN_ID" >&2
  exit 1
fi

if [ ! -d "$WORKSPACE" ]; then
  echo "Workspace not found: $WORKSPACE" >&2
  exit 1
fi

if [ ! -f "$WORKSPACE/db/runs.sqlite" ]; then
  echo "runs.sqlite not found in workspace: $WORKSPACE" >&2
  exit 1
fi

existing_pid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
if [ -n "$existing_pid" ]; then
  if ps -p "$existing_pid" -o command= | grep -q "kernelswarm serve-dashboard"; then
    echo "Stopping existing dashboard on port $PORT (pid=$existing_pid)" >&2
    kill "$existing_pid" >/dev/null 2>&1 || true
    sleep 1
  else
    echo "Port $PORT is in use by pid=$existing_pid. Set KERNELSWARM_DASHBOARD_PORT to another port." >&2
    exit 1
  fi
fi

dashboard_url="http://${HOST}:${PORT}"
if [ -n "$RUN_ID" ]; then
  dashboard_url="${dashboard_url}/?run_id=${RUN_ID}"
fi

echo "Starting KernelSwarm dashboard on ${dashboard_url}" >&2
echo "Workspace: ${WORKSPACE}" >&2
if [ -n "$RUN_ID" ]; then
  echo "Initial run_id: ${RUN_ID}" >&2
fi
echo "dashboard_url=${dashboard_url}"
echo "workspace=${WORKSPACE}"
if [ -n "$RUN_ID" ]; then
  echo "run_id=${RUN_ID}"
fi

if [ "$DASHBOARD_DRY_RUN" = "1" ]; then
  exit 0
fi

# --- Auto-build frontend if needed ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/dashboard"

if [ -f "$DASHBOARD_DIR/package.json" ]; then
  NEEDS_BUILD=0
  if [ ! -f "$DASHBOARD_DIR/dist/index.html" ]; then
    NEEDS_BUILD=1
  elif [ "$DASHBOARD_DIR/package.json" -nt "$DASHBOARD_DIR/dist/index.html" ]; then
    NEEDS_BUILD=1
  else
    # Rebuild if any source file is newer than the build
    if [ -d "$DASHBOARD_DIR/src" ]; then
      newer="$(find "$DASHBOARD_DIR/src" -type f -newer "$DASHBOARD_DIR/dist/index.html" -print -quit 2>/dev/null || true)"
      if [ -n "$newer" ]; then
        NEEDS_BUILD=1
      fi
    fi
  fi

  if [ "$NEEDS_BUILD" = "1" ]; then
    echo "Building dashboard frontend..." >&2
    if ! command -v bun >/dev/null 2>&1; then
      echo "Warning: bun not found, skipping frontend build. Install bun for the new dashboard UI." >&2
    else
      (cd "$DASHBOARD_DIR" && bun install --frozen-lockfile 2>/dev/null || bun install) >&2
      (cd "$DASHBOARD_DIR" && bun run build) >&2
      echo "Frontend built successfully." >&2
    fi
  fi
fi

if command -v uv >/dev/null 2>&1 && [ "${KERNELSWARM_NO_UV:-0}" != "1" ]; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
  exec uv run python -m kernelswarm serve-dashboard \
    --workspace "$WORKSPACE" \
    --host "$HOST" \
    --port "$PORT"
fi

if [ -x ".venv/bin/python" ]; then
  exec PYTHONPATH=src .venv/bin/python -m kernelswarm serve-dashboard \
    --workspace "$WORKSPACE" \
    --host "$HOST" \
    --port "$PORT"
fi

echo "Neither uv nor .venv fallback is available." >&2
exit 1
