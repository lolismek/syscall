#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${1:-.runs/smoke}"

run_args=(
  run-vector-add
  --workspace "$WORKSPACE"
  --top-k-full 1
  --seed-count 3
  --quick-iters 5
  --full-iters 8
  --quick-size 5000
  --full-size 10000
)

if command -v uv >/dev/null 2>&1 && [ "${KERNELSWARM_NO_UV:-0}" != "1" ]; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
  uv_log="$(mktemp -t kernelswarm_uv_log.XXXXXX)"
  if uv run kernelswarm "${run_args[@]}" >"$uv_log" 2>&1; then
    cat "$uv_log"
    rm -f "$uv_log"
    exit 0
  else
    echo "uv run failed; falling back to .venv/python." >&2
    sed -n '1,10p' "$uv_log" >&2 || true
    rm -f "$uv_log"
  fi
fi

if [ -x ".venv/bin/python" ]; then
  PYTHONPATH=src .venv/bin/python -m kernelswarm "${run_args[@]}"
  exit 0
fi

echo "Neither uv run nor .venv fallback is available for smoke test." >&2
exit 1
