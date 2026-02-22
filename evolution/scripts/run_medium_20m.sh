#!/usr/bin/env bash
set -euo pipefail

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

WORKSPACE="${1:-${KERNELSWARM_WORKSPACE:-.runs/search-medium-20m}}"
REMOTE_EVAL_URL="${KERNELSWARM_REMOTE_EVAL_URL:-http://127.0.0.1:18080}"
REMOTE_EVAL_URL_PRIMARY="${REMOTE_EVAL_URL%%,*}"
PROBLEM_ID="${KERNELSWARM_PROBLEM_ID:-kernelbench_v1}"
BACKEND="${KERNELSWARM_BACKEND:-cuda}"
MAX_MINUTES="${KERNELSWARM_MAX_MINUTES:-20}"
MAX_ITERATIONS="${KERNELSWARM_MAX_ITERATIONS:-5000}"
TOKEN_BUDGET="${KERNELSWARM_TOKEN_BUDGET:-2000000}"
GENERATORS="${KERNELSWARM_GENERATORS:-24}"
JUDGES="${KERNELSWARM_JUDGES:-10}"
NEMOTRON_PROVIDER="${KERNELSWARM_NEMOTRON_PROVIDER:-deepinfra}"
NEMOTRON_MAX_CONCURRENT_REQUESTS="${KERNELSWARM_NEMOTRON_MAX_CONCURRENT_REQUESTS:-10}"
KB_LEVEL="${KERNELSWARM_KB_LEVEL:-1}"
KB_PROBLEM_ID="${KERNELSWARM_KB_PROBLEM_ID:-40}"
KB_DATASET_SOURCE="${KERNELSWARM_KB_DATASET_SOURCE:-local}"
KB_REPO_PATH="${KERNELSWARM_KB_REPO_PATH:-$HOME/KernelBench}"
KB_PRECISION="${KERNELSWARM_KB_PRECISION:-fp32}"
KB_QUICK_PERF_TRIALS="${KERNELSWARM_KB_QUICK_PERF_TRIALS:-4}"
KB_FULL_PERF_TRIALS="${KERNELSWARM_KB_FULL_PERF_TRIALS:-20}"
PROPOSAL_WORKERS="${KERNELSWARM_PROPOSAL_WORKERS:-10}"
QUICK_EVAL_WORKERS="${KERNELSWARM_QUICK_EVAL_WORKERS:-16}"
FULL_EVAL_WORKERS="${KERNELSWARM_FULL_EVAL_WORKERS:-6}"
MAX_INFLIGHT_PROPOSALS="${KERNELSWARM_MAX_INFLIGHT_PROPOSALS:-128}"
MAX_INFLIGHT_QUICK_EVALS="${KERNELSWARM_MAX_INFLIGHT_QUICK_EVALS:-48}"
MAX_INFLIGHT_FULL_EVALS="${KERNELSWARM_MAX_INFLIGHT_FULL_EVALS:-16}"
PERIODIC_FULL_EVAL_EVERY_QUICK="${KERNELSWARM_PERIODIC_FULL_EVAL_EVERY_QUICK:-40}"
FORCE_FIRST_FULL_PER_ISLAND="${KERNELSWARM_FORCE_FIRST_FULL_PER_ISLAND:-1}"
AUTO_TUNNEL="${KERNELSWARM_AUTO_TUNNEL:-1}"
TUNNEL_TARGET="${KERNELSWARM_TUNNEL_TARGET:-kernel-swarm-eval-new-2}"
TUNNEL_MODE="${KERNELSWARM_TUNNEL_MODE:-ssh}"
TUNNEL_REMOTE_HOST="${KERNELSWARM_TUNNEL_REMOTE_HOST:-127.0.0.1}"
TUNNEL_REMOTE_PORT="${KERNELSWARM_TUNNEL_REMOTE_PORT:-8080}"
TUNNEL_SSH_OPTS_RAW="${KERNELSWARM_TUNNEL_SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null}"
IFS=' ' read -r -a TUNNEL_SSH_OPTS <<< "$TUNNEL_SSH_OPTS_RAW"
TUNNEL_PID=""
STARTED_TUNNEL=0
RUN_PID=""
STARTED_RUN=0
CLEANUP_DONE=0
AUTO_REMOTE_KB_PATH=0

resolve_remote_kb_repo_path() {
  if [ -n "${KERNELSWARM_REMOTE_KB_REPO_PATH:-}" ]; then
    echo "$KERNELSWARM_REMOTE_KB_REPO_PATH"
    return 0
  fi

  if [ "$TUNNEL_MODE" = "ssh" ] && [[ "$TUNNEL_TARGET" == *@* ]]; then
    local remote_user
    remote_user="${TUNNEL_TARGET%@*}"
    if [ -n "$remote_user" ]; then
      echo "/home/${remote_user}/KernelBench"
      return 0
    fi
  fi

  echo "/home/shadeform/KernelBench"
  return 0
}

cleanup_tunnel() {
  if [ "$STARTED_TUNNEL" = "1" ] && [ -n "$TUNNEL_PID" ]; then
    if kill -0 "$TUNNEL_PID" >/dev/null 2>&1; then
      kill "$TUNNEL_PID" >/dev/null 2>&1 || true
      wait "$TUNNEL_PID" >/dev/null 2>&1 || true
    fi
  fi
}

child_pids() {
  local pid="$1"
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -P "$pid" 2>/dev/null || true
    return 0
  fi
  ps -o pid= --ppid "$pid" 2>/dev/null | awk '{print $1}'
}

kill_process_tree() {
  local pid="$1"
  local signal="${2:-TERM}"
  local child
  for child in $(child_pids "$pid"); do
    kill_process_tree "$child" "$signal"
  done
  kill "-$signal" "$pid" >/dev/null 2>&1 || true
}

cleanup_run() {
  if [ "$STARTED_RUN" != "1" ] || [ -z "$RUN_PID" ]; then
    return 0
  fi

  if ! kill -0 "$RUN_PID" >/dev/null 2>&1; then
    STARTED_RUN=0
    RUN_PID=""
    return 0
  fi

  echo "Stopping swarm process tree (pid=$RUN_PID)..." >&2
  kill_process_tree "$RUN_PID" TERM

  local i
  for i in 1 2 3 4 5; do
    if ! kill -0 "$RUN_PID" >/dev/null 2>&1; then
      STARTED_RUN=0
      RUN_PID=""
      return 0
    fi
    sleep 1
  done

  if kill -0 "$RUN_PID" >/dev/null 2>&1; then
    echo "Swarm process still running; force killing (pid=$RUN_PID)." >&2
    kill_process_tree "$RUN_PID" KILL
    sleep 1
  fi

  wait "$RUN_PID" >/dev/null 2>&1 || true
  STARTED_RUN=0
  RUN_PID=""
}

cleanup_all() {
  if [ "$CLEANUP_DONE" = "1" ]; then
    return 0
  fi
  CLEANUP_DONE=1
  set +e
  cleanup_run
  cleanup_tunnel
}

handle_signal() {
  local signal="$1"
  echo "Received ${signal}; shutting down..." >&2
  cleanup_all
  if [ "$signal" = "INT" ]; then
    exit 130
  fi
  exit 143
}

ensure_remote_eval_ready() {
  if [ -z "$REMOTE_EVAL_URL" ]; then
    return 0
  fi

  local health_url
  health_url="${REMOTE_EVAL_URL_PRIMARY%/}/healthz"
  if curl -fsS "$health_url" >/dev/null 2>&1; then
    return 0
  fi

  if [ "$AUTO_TUNNEL" != "1" ]; then
    echo "Remote eval is unreachable at: $health_url" >&2
    echo "Set KERNELSWARM_AUTO_TUNNEL=1 or start the tunnel manually." >&2
    return 1
  fi

  local url_no_scheme hostport host port
  url_no_scheme="${REMOTE_EVAL_URL_PRIMARY#http://}"
  hostport="${url_no_scheme%%/*}"
  host="${hostport%%:*}"
  port="${hostport##*:}"

  if [ "$host" != "127.0.0.1" ] && [ "$host" != "localhost" ]; then
    echo "Auto tunnel only supports localhost remote-eval URLs." >&2
    echo "Current remote_eval_url=$REMOTE_EVAL_URL_PRIMARY" >&2
    return 1
  fi

  if ! [[ "$port" =~ ^[0-9]+$ ]]; then
    echo "Unable to infer local tunnel port from remote_eval_url=$REMOTE_EVAL_URL_PRIMARY" >&2
    return 1
  fi

  local tunnel_log
  tunnel_log="/tmp/kernelswarm_tunnel_${port}.log"
  if [ "$TUNNEL_MODE" = "brev" ]; then
    echo "Remote eval unreachable, starting Brev port-forward: localhost:${port} -> ${TUNNEL_TARGET}:${TUNNEL_REMOTE_PORT}" >&2
    brev port-forward "$TUNNEL_TARGET" -p "${port}:${TUNNEL_REMOTE_PORT}" >"$tunnel_log" 2>&1 &
  else
    echo "Remote eval unreachable, starting SSH tunnel: localhost:${port} -> ${TUNNEL_TARGET}:${TUNNEL_REMOTE_PORT}" >&2
    ssh "${TUNNEL_SSH_OPTS[@]}" -N -L "${port}:${TUNNEL_REMOTE_HOST}:${TUNNEL_REMOTE_PORT}" "$TUNNEL_TARGET" >"$tunnel_log" 2>&1 &
  fi
  TUNNEL_PID="$!"
  STARTED_TUNNEL=1

  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$TUNNEL_PID" >/dev/null 2>&1; then
      echo "Tunnel process exited early. Log tail:" >&2
      tail -n 30 "$tunnel_log" >&2 || true
      return 1
    fi
    if curl -fsS "$health_url" >/dev/null 2>&1; then
      echo "Tunnel established (pid=$TUNNEL_PID)." >&2
      return 0
    fi
    sleep 1
  done

  echo "Tunnel started but remote eval health still failing at $health_url" >&2
  tail -n 30 "$tunnel_log" >&2 || true
  return 1
}

# If running against remote eval and no explicit KB path was provided, always use
# the remote path so the eval worker resolves the dataset correctly.
if [ -n "$REMOTE_EVAL_URL" ] && [ -z "${KERNELSWARM_KB_REPO_PATH:-}" ]; then
  KB_REPO_PATH="$(resolve_remote_kb_repo_path)"
  AUTO_REMOTE_KB_PATH=1
fi

if [ ! -f ".env" ]; then
  echo ".env not found in repo root; create it with DEEPINFRA_API_KEY=... or NVIDIA_API_KEY=..." >&2
  exit 1
fi

if ! grep -qE '^(DEEPINFRA_API_KEY|NVIDIA_API_KEY)=' .env; then
  echo "Neither DEEPINFRA_API_KEY nor NVIDIA_API_KEY found in .env" >&2
  exit 1
fi

if [ "$KB_DATASET_SOURCE" = "local" ] && [ ! -d "$KB_REPO_PATH" ]; then
  if [ -n "$REMOTE_EVAL_URL" ]; then
    echo "Local KernelBench path not found: $KB_REPO_PATH" >&2
    echo "Continuing because remote eval is enabled; this path is expected to exist on the remote worker." >&2
  else
    echo "KernelBench repo path not found: $KB_REPO_PATH" >&2
    echo "Set KERNELSWARM_KB_REPO_PATH or use KERNELSWARM_KB_DATASET_SOURCE=huggingface" >&2
    exit 1
  fi
fi

trap cleanup_all EXIT
trap 'handle_signal INT' INT
trap 'handle_signal TERM' TERM
ensure_remote_eval_ready

echo "Run config:" >&2
echo "  workspace=$WORKSPACE" >&2
echo "  remote_eval_url=$REMOTE_EVAL_URL" >&2
echo "  remote_eval_primary=$REMOTE_EVAL_URL_PRIMARY" >&2
echo "  problem_id=$PROBLEM_ID backend=$BACKEND" >&2
echo "  agents generators=$GENERATORS judges=$JUDGES" >&2
echo "  llm_provider=$NEMOTRON_PROVIDER llm_max_concurrent_requests=$NEMOTRON_MAX_CONCURRENT_REQUESTS" >&2
echo "  kb_level=$KB_LEVEL kb_problem_id=$KB_PROBLEM_ID kb_precision=$KB_PRECISION" >&2
echo "  kb_repo_path=$KB_REPO_PATH" >&2
echo "  workers proposal=$PROPOSAL_WORKERS quick_eval=$QUICK_EVAL_WORKERS full_eval=$FULL_EVAL_WORKERS" >&2
echo "  inflight proposals=$MAX_INFLIGHT_PROPOSALS quick=$MAX_INFLIGHT_QUICK_EVALS full=$MAX_INFLIGHT_FULL_EVALS" >&2
echo "  full_eval_policy periodic_every_quick=$PERIODIC_FULL_EVAL_EVERY_QUICK force_first_per_island=$FORCE_FIRST_FULL_PER_ISLAND" >&2
echo "  auto_tunnel=$AUTO_TUNNEL mode=$TUNNEL_MODE target=$TUNNEL_TARGET" >&2
if [ "$AUTO_REMOTE_KB_PATH" = "1" ]; then
  echo "  note=auto-selected Brev KernelBench path for remote eval" >&2
fi
echo "  max_minutes=$MAX_MINUTES max_iterations=$MAX_ITERATIONS token_budget=$TOKEN_BUDGET" >&2
echo >&2
echo "Tip: in another terminal run ./scripts/serve_dashboard.sh \"$WORKSPACE\"" >&2

run_args=(
  python -m kernelswarm run-swarm-search
  --workspace "$WORKSPACE"
  --problem-id "$PROBLEM_ID"
  --backend "$BACKEND"
  --remote-eval-url "$REMOTE_EVAL_URL"
  --max-minutes "$MAX_MINUTES"
  --max-iterations "$MAX_ITERATIONS"
  --token-budget "$TOKEN_BUDGET"
  --generators "$GENERATORS"
  --judges "$JUDGES"
  --nemotron-provider "$NEMOTRON_PROVIDER"
  --nemotron-max-concurrent-requests "$NEMOTRON_MAX_CONCURRENT_REQUESTS"
  --proposal-workers "$PROPOSAL_WORKERS"
  --quick-eval-workers "$QUICK_EVAL_WORKERS"
  --full-eval-workers "$FULL_EVAL_WORKERS"
  --max-inflight-proposals "$MAX_INFLIGHT_PROPOSALS"
  --max-inflight-quick-evals "$MAX_INFLIGHT_QUICK_EVALS"
  --max-inflight-full-evals "$MAX_INFLIGHT_FULL_EVALS"
  --periodic-full-eval-every-quick "$PERIODIC_FULL_EVAL_EVERY_QUICK"
  --kb-level "$KB_LEVEL"
  --kb-problem-id "$KB_PROBLEM_ID"
  --kb-dataset-source "$KB_DATASET_SOURCE"
  --kb-repo-path "$KB_REPO_PATH"
  --kb-precision "$KB_PRECISION"
  --kb-quick-perf-trials "$KB_QUICK_PERF_TRIALS"
  --kb-full-perf-trials "$KB_FULL_PERF_TRIALS"
)

if command -v uv >/dev/null 2>&1 && [ "${KERNELSWARM_NO_UV:-0}" != "1" ]; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
  if [ "$FORCE_FIRST_FULL_PER_ISLAND" = "0" ]; then
    run_args+=(--no-force-first-full-per-island)
  fi
  uv run "${run_args[@]}" &
  RUN_PID="$!"
  STARTED_RUN=1
  if wait "$RUN_PID"; then
    run_exit_code=0
  else
    run_exit_code=$?
  fi
  STARTED_RUN=0
  RUN_PID=""
  exit "$run_exit_code"
fi

if [ -x ".venv/bin/python" ]; then
  fallback_args=(
    -m kernelswarm run-swarm-search
    --workspace "$WORKSPACE"
    --problem-id "$PROBLEM_ID"
    --backend "$BACKEND"
    --remote-eval-url "$REMOTE_EVAL_URL"
    --max-minutes "$MAX_MINUTES"
    --max-iterations "$MAX_ITERATIONS"
    --token-budget "$TOKEN_BUDGET"
    --generators "$GENERATORS"
    --judges "$JUDGES"
    --nemotron-provider "$NEMOTRON_PROVIDER"
    --nemotron-max-concurrent-requests "$NEMOTRON_MAX_CONCURRENT_REQUESTS"
    --proposal-workers "$PROPOSAL_WORKERS"
    --quick-eval-workers "$QUICK_EVAL_WORKERS"
    --full-eval-workers "$FULL_EVAL_WORKERS"
    --max-inflight-proposals "$MAX_INFLIGHT_PROPOSALS"
    --max-inflight-quick-evals "$MAX_INFLIGHT_QUICK_EVALS"
    --max-inflight-full-evals "$MAX_INFLIGHT_FULL_EVALS"
    --periodic-full-eval-every-quick "$PERIODIC_FULL_EVAL_EVERY_QUICK"
    --kb-level "$KB_LEVEL"
    --kb-problem-id "$KB_PROBLEM_ID"
    --kb-dataset-source "$KB_DATASET_SOURCE"
    --kb-repo-path "$KB_REPO_PATH"
    --kb-precision "$KB_PRECISION"
    --kb-quick-perf-trials "$KB_QUICK_PERF_TRIALS"
    --kb-full-perf-trials "$KB_FULL_PERF_TRIALS"
  )
  if [ "$FORCE_FIRST_FULL_PER_ISLAND" = "0" ]; then
    fallback_args+=(--no-force-first-full-per-island)
  fi
  PYTHONPATH=src .venv/bin/python "${fallback_args[@]}" &
  RUN_PID="$!"
  STARTED_RUN=1
  if wait "$RUN_PID"; then
    run_exit_code=0
  else
    run_exit_code=$?
  fi
  STARTED_RUN=0
  RUN_PID=""
  exit "$run_exit_code"
fi

echo "Neither uv nor .venv fallback is available." >&2
exit 1
