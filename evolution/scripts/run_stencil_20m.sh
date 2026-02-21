#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Stencil is cheap enough to score full-stage more often by default.
: "${KERNELSWARM_PERIODIC_FULL_EVAL_EVERY_QUICK:=12}"
export KERNELSWARM_PERIODIC_FULL_EVAL_EVERY_QUICK
exec "$SCRIPT_DIR/run_alt_problem_20m.sh" stencil2d_v1 "${1:-.runs/search-stencil-20m}"
