#!/usr/bin/env bash
set -euo pipefail

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required" >&2
  exit 1
fi

ROOT_WORKSPACE="${1:-${KERNELSWARM_SUITE_WORKSPACE:-.runs/suite-v1}}"
PROBLEMS_RAW="${KERNELSWARM_SUITE_PROBLEMS:-vector_add_v1,reduction_v1,stencil2d_v1}"
SEEDS_RAW="${KERNELSWARM_SUITE_SEEDS:-41,42,43}"
MAX_MINUTES="${KERNELSWARM_SUITE_MAX_MINUTES:-6}"
MAX_ITERATIONS="${KERNELSWARM_SUITE_MAX_ITERATIONS:-240}"
TOKEN_BUDGET="${KERNELSWARM_SUITE_TOKEN_BUDGET:-300000}"
REMOTE_EVAL_URL="${KERNELSWARM_REMOTE_EVAL_URL:-}"
NEMOTRON_PROVIDER="${KERNELSWARM_NEMOTRON_PROVIDER:-deepinfra}"
NEMOTRON_MAX_CONCURRENT_REQUESTS="${KERNELSWARM_NEMOTRON_MAX_CONCURRENT_REQUESTS:-32}"
GENERATORS="${KERNELSWARM_GENERATORS:-32}"
JUDGES="${KERNELSWARM_JUDGES:-32}"
PROPOSAL_WORKERS="${KERNELSWARM_PROPOSAL_WORKERS:-32}"
QUICK_EVAL_WORKERS="${KERNELSWARM_QUICK_EVAL_WORKERS:-16}"
FULL_EVAL_WORKERS="${KERNELSWARM_FULL_EVAL_WORKERS:-6}"

IFS=',' read -r -a PROBLEMS <<< "$PROBLEMS_RAW"
IFS=',' read -r -a SEEDS <<< "$SEEDS_RAW"

mkdir -p "$ROOT_WORKSPACE"
RESULTS_TSV="$ROOT_WORKSPACE/results.tsv"
printf "problem\tseed\trun_id\tworkspace\treport\tbest_fitness\tquick_scored\tfull_scored\n" > "$RESULTS_TSV"

for problem in "${PROBLEMS[@]}"; do
  for seed in "${SEEDS[@]}"; do
    ws="$ROOT_WORKSPACE/${problem}/seed-${seed}"
    mkdir -p "$ws"
    echo "=== problem=${problem} seed=${seed} workspace=${ws}" >&2

    args=(
      python -m kernelswarm run-swarm-search
      --workspace "$ws"
      --problem-id "$problem"
      --seed "$seed"
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
    )
    if [ -n "$REMOTE_EVAL_URL" ]; then
      args+=(--remote-eval-url "$REMOTE_EVAL_URL")
    fi

    output="$(uv run "${args[@]}")"
    echo "$output"

    run_id="$(printf "%s\n" "$output" | awk -F= '/^run_id=/{print $2}' | tail -n 1)"
    report="$(printf "%s\n" "$output" | awk -F= '/^report=/{print $2}' | tail -n 1)"
    best_fitness="$(printf "%s\n" "$output" | awk -F= '/^best_fitness=/{print $2}' | tail -n 1)"
    quick_scored="$(printf "%s\n" "$output" | awk -F= '/^quick_scored=/{print $2}' | tail -n 1)"
    full_scored="$(printf "%s\n" "$output" | awk -F= '/^full_scored=/{print $2}' | tail -n 1)"

    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "$problem" "$seed" "$run_id" "$ws" "$report" "$best_fitness" "$quick_scored" "$full_scored" >> "$RESULTS_TSV"
  done
done

echo "suite_results=$RESULTS_TSV"
