#!/usr/bin/env bash
# Setup script for a fresh Brev L40S eval worker box.
# Run on the remote machine: bash setup_eval_worker.sh
set -euo pipefail
export UV_NO_PROGRESS=1
export PIP_PROGRESS_BAR=off

REPO_DIR="$HOME/syscall/evolution"
KB_DIR="$HOME/KernelBench"
EVAL_PORT="${EVAL_PORT:-8080}"

echo "=== Installing uv ==="
if ! command -v uv >/dev/null 2>&1 && [ ! -f "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

echo "=== Cloning repos ==="
if [ ! -d "$REPO_DIR" ]; then
  git clone https://github.com/lolismek/syscall.git "$HOME/syscall"
fi
cd "$REPO_DIR"
git pull --ff-only origin "$(git branch --show-current)" 2>&1 || true

if [ ! -d "$KB_DIR" ]; then
  git clone https://github.com/ScalingIntelligence/KernelBench.git "$KB_DIR"
fi

echo "=== Creating venv with Python 3.12 ==="
if [ ! -d .venv ]; then
  uv venv --python 3.12 .venv
fi

echo "=== Installing PyTorch (cu124 to match system CUDA 12.4) ==="
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/cu124 \
  torch==2.6.0 torchvision==0.21.0

echo "=== Installing project dependencies ==="
uv pip install --python .venv/bin/python pyyaml triton

echo "=== Installing KernelBench ==="
uv pip install --python .venv/bin/python -e "$KB_DIR"

echo "=== Verifying installation ==="
PYTHONPATH=src .venv/bin/python - <<'PY'
import torch
print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"CUDA version: {torch.version.cuda}")
import yaml
print("PyYAML: OK")
import triton
print(f"Triton: {triton.__version__}")
import importlib
kb = importlib.import_module("kernelbench.eval")
print(f"KernelBench: {kb.__name__}")
from kernelswarm.remote import EvalWorkerService
print("kernelswarm: OK")
PY

echo "=== Starting eval worker ==="
# Kill any existing eval worker
pkill -f "kernelswarm serve-eval-worker" 2>/dev/null || true
sleep 1

# Start in tmux so it persists after SSH disconnects
if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t eval 2>/dev/null || true
  tmux new-session -d -s eval \
    "cd $REPO_DIR && PYTHONPATH=src .venv/bin/python -m kernelswarm serve-eval-worker --host 0.0.0.0 --port $EVAL_PORT 2>&1 | tee /tmp/eval-worker.log"
  echo "Eval worker started in tmux session 'eval'"
else
  cd "$REPO_DIR"
  nohup env PYTHONPATH=src .venv/bin/python -m kernelswarm serve-eval-worker \
    --host 0.0.0.0 --port "$EVAL_PORT" >/tmp/eval-worker.log 2>&1 &
  echo "Eval worker started (pid=$!)"
fi

# Wait for health
echo "Waiting for eval worker to be ready..."
for i in $(seq 1 15); do
  if curl -fsS "http://127.0.0.1:$EVAL_PORT/healthz" 2>/dev/null; then
    echo ""
    echo "=== Eval worker is healthy on port $EVAL_PORT ==="
    exit 0
  fi
  sleep 1
done

echo "WARNING: eval worker not healthy after 15s"
echo "Log tail:"
tail -30 /tmp/eval-worker.log 2>/dev/null
exit 1
