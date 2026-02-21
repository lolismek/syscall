#!/usr/bin/env bash
set -euo pipefail

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PROBLEM_ID="${1:-${KERNELSWARM_ALT_PROBLEM_ID:-reduction_v1}}"
case "$PROBLEM_ID" in
  reduction_v1|stencil2d_v1)
    ;;
  *)
    echo "Unsupported alt problem: $PROBLEM_ID" >&2
    echo "Supported: reduction_v1, stencil2d_v1" >&2
    exit 1
    ;;
esac

WORKSPACE="${2:-${KERNELSWARM_WORKSPACE:-.runs/search-${PROBLEM_ID}-20m}}"
MAX_MINUTES="${KERNELSWARM_MAX_MINUTES:-20}"
MAX_ITERATIONS="${KERNELSWARM_MAX_ITERATIONS:-5000}"
TOKEN_BUDGET="${KERNELSWARM_TOKEN_BUDGET:-2000000}"
GENERATORS="${KERNELSWARM_GENERATORS:-32}"
JUDGES="${KERNELSWARM_JUDGES:-32}"
LLM_ENABLED="${KERNELSWARM_LLM_ENABLED:-1}"
NEMOTRON_PROVIDER="${KERNELSWARM_NEMOTRON_PROVIDER:-deepinfra}"
NEMOTRON_MODEL="${KERNELSWARM_NEMOTRON_MODEL:-nvidia/Nemotron-3-Nano-30B-A3B}"
NEMOTRON_MAX_CONCURRENT_REQUESTS="${KERNELSWARM_NEMOTRON_MAX_CONCURRENT_REQUESTS:-32}"
PROPOSAL_WORKERS="${KERNELSWARM_PROPOSAL_WORKERS:-32}"
QUICK_EVAL_WORKERS="${KERNELSWARM_QUICK_EVAL_WORKERS:-16}"
FULL_EVAL_WORKERS="${KERNELSWARM_FULL_EVAL_WORKERS:-6}"
MAX_INFLIGHT_PROPOSALS="${KERNELSWARM_MAX_INFLIGHT_PROPOSALS:-128}"
MAX_INFLIGHT_QUICK_EVALS="${KERNELSWARM_MAX_INFLIGHT_QUICK_EVALS:-48}"
MAX_INFLIGHT_FULL_EVALS="${KERNELSWARM_MAX_INFLIGHT_FULL_EVALS:-16}"
PERIODIC_FULL_EVAL_EVERY_QUICK="${KERNELSWARM_PERIODIC_FULL_EVAL_EVERY_QUICK:-40}"
FORCE_FIRST_FULL_PER_ISLAND="${KERNELSWARM_FORCE_FIRST_FULL_PER_ISLAND:-1}"
SEED_COUNT="${KERNELSWARM_SEED_COUNT:-4}"
QUICK_SIZE="${KERNELSWARM_QUICK_SIZE:-20000}"
FULL_SIZE="${KERNELSWARM_FULL_SIZE:-100000}"
QUICK_ITERS="${KERNELSWARM_QUICK_ITERS:-15}"
FULL_ITERS="${KERNELSWARM_FULL_ITERS:-40}"
QUICK_WARMUP="${KERNELSWARM_QUICK_WARMUP:-3}"
FULL_WARMUP="${KERNELSWARM_FULL_WARMUP:-6}"

if [ "$PROBLEM_ID" = "stencil2d_v1" ]; then
  QUICK_SIZE="${KERNELSWARM_QUICK_SIZE:-96}"
  FULL_SIZE="${KERNELSWARM_FULL_SIZE:-192}"
fi

echo "Run config:" >&2
echo "  workspace=$WORKSPACE" >&2
echo "  problem_id=$PROBLEM_ID backend=python-sim" >&2
echo "  max_minutes=$MAX_MINUTES max_iterations=$MAX_ITERATIONS token_budget=$TOKEN_BUDGET" >&2
echo "  agents generators=$GENERATORS judges=$JUDGES llm_enabled=$LLM_ENABLED" >&2
echo "  llm_provider=$NEMOTRON_PROVIDER llm_model=$NEMOTRON_MODEL llm_max_concurrent_requests=$NEMOTRON_MAX_CONCURRENT_REQUESTS" >&2
echo "  workers proposal=$PROPOSAL_WORKERS quick_eval=$QUICK_EVAL_WORKERS full_eval=$FULL_EVAL_WORKERS" >&2
echo "  inflight proposals=$MAX_INFLIGHT_PROPOSALS quick=$MAX_INFLIGHT_QUICK_EVALS full=$MAX_INFLIGHT_FULL_EVALS" >&2
echo "  seed_count=$SEED_COUNT quick_size=$QUICK_SIZE full_size=$FULL_SIZE quick_iters=$QUICK_ITERS full_iters=$FULL_ITERS" >&2
echo >&2
echo "Tip: in another terminal run ./scripts/serve_dashboard.sh \"$WORKSPACE\"" >&2

run_args=(
  python -m kernelswarm run-swarm-search
  --workspace "$WORKSPACE"
  --problem-id "$PROBLEM_ID"
  --backend python-sim
  --max-minutes "$MAX_MINUTES"
  --max-iterations "$MAX_ITERATIONS"
  --token-budget "$TOKEN_BUDGET"
  --generators "$GENERATORS"
  --judges "$JUDGES"
  --nemotron-provider "$NEMOTRON_PROVIDER"
  --nemotron-model "$NEMOTRON_MODEL"
  --nemotron-max-concurrent-requests "$NEMOTRON_MAX_CONCURRENT_REQUESTS"
  --proposal-workers "$PROPOSAL_WORKERS"
  --quick-eval-workers "$QUICK_EVAL_WORKERS"
  --full-eval-workers "$FULL_EVAL_WORKERS"
  --max-inflight-proposals "$MAX_INFLIGHT_PROPOSALS"
  --max-inflight-quick-evals "$MAX_INFLIGHT_QUICK_EVALS"
  --max-inflight-full-evals "$MAX_INFLIGHT_FULL_EVALS"
  --periodic-full-eval-every-quick "$PERIODIC_FULL_EVAL_EVERY_QUICK"
  --seed-count "$SEED_COUNT"
  --quick-size "$QUICK_SIZE"
  --full-size "$FULL_SIZE"
  --quick-iters "$QUICK_ITERS"
  --full-iters "$FULL_ITERS"
  --quick-warmup "$QUICK_WARMUP"
  --full-warmup "$FULL_WARMUP"
)

if [ "$FORCE_FIRST_FULL_PER_ISLAND" = "0" ]; then
  run_args+=(--no-force-first-full-per-island)
fi

if [ "$LLM_ENABLED" = "0" ]; then
  run_args+=(--llm-disabled)
fi

if command -v uv >/dev/null 2>&1 && [ "${KERNELSWARM_NO_UV:-0}" != "1" ]; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
  exec uv run "${run_args[@]}"
fi

if [ -x ".venv/bin/python" ]; then
  fallback_args=(
    -m kernelswarm run-swarm-search
    --workspace "$WORKSPACE"
    --problem-id "$PROBLEM_ID"
    --backend python-sim
    --max-minutes "$MAX_MINUTES"
    --max-iterations "$MAX_ITERATIONS"
    --token-budget "$TOKEN_BUDGET"
    --generators "$GENERATORS"
    --judges "$JUDGES"
    --nemotron-provider "$NEMOTRON_PROVIDER"
    --nemotron-model "$NEMOTRON_MODEL"
    --nemotron-max-concurrent-requests "$NEMOTRON_MAX_CONCURRENT_REQUESTS"
    --proposal-workers "$PROPOSAL_WORKERS"
    --quick-eval-workers "$QUICK_EVAL_WORKERS"
    --full-eval-workers "$FULL_EVAL_WORKERS"
    --max-inflight-proposals "$MAX_INFLIGHT_PROPOSALS"
    --max-inflight-quick-evals "$MAX_INFLIGHT_QUICK_EVALS"
    --max-inflight-full-evals "$MAX_INFLIGHT_FULL_EVALS"
    --periodic-full-eval-every-quick "$PERIODIC_FULL_EVAL_EVERY_QUICK"
    --seed-count "$SEED_COUNT"
    --quick-size "$QUICK_SIZE"
    --full-size "$FULL_SIZE"
    --quick-iters "$QUICK_ITERS"
    --full-iters "$FULL_ITERS"
    --quick-warmup "$QUICK_WARMUP"
    --full-warmup "$FULL_WARMUP"
  )
  if [ "$FORCE_FIRST_FULL_PER_ISLAND" = "0" ]; then
    fallback_args+=(--no-force-first-full-per-island)
  fi
  if [ "$LLM_ENABLED" = "0" ]; then
    fallback_args+=(--llm-disabled)
  fi
  exec PYTHONPATH=src .venv/bin/python "${fallback_args[@]}"
fi

echo "Neither uv nor .venv fallback is available." >&2
exit 1
