#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required" >&2
  exit 1
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "Detected GPU:" >&2
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
else
  echo "nvidia-smi not found; continuing in CPU simulation mode" >&2
fi

if command -v uv >/dev/null 2>&1; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
  if ! uv run python -m compileall src; then
    echo "uv compileall failed; attempting .venv fallback" >&2
    if [ -x ".venv/bin/python" ]; then
      PYTHONPATH=src .venv/bin/python -m compileall src
    else
      exit 1
    fi
  fi
elif [ -x ".venv/bin/python" ]; then
  PYTHONPATH=src .venv/bin/python -m compileall src
else
  echo "No uv or .venv interpreter found" >&2
  exit 1
fi

./scripts/smoke_test.sh "${1:-.runs/bootstrap}"
