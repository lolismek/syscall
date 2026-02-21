# KernelSwarm

This repository contains the v1 implementation foundation for KernelSwarm:

- canonical schemas and deterministic hashing
- `OptimizationProblem` SDK
- sequential evaluation pipeline (`build -> validate -> benchmark -> score -> persist`)
- SQLite persistence and artifact storage
- reproducibility manifest capture
- example `vector_add` plugin

## Layout

- `src/kernelswarm/models.py`: canonical run/candidate/result/state contracts
- `src/kernelswarm/sdk.py`: problem plugin protocol
- `src/kernelswarm/pipeline.py`: single-worker orchestration
- `src/kernelswarm/persistence.py`: SQLite store
- `src/kernelswarm/manifest.py`: environment + toolchain manifest
- `src/kernelswarm/plugins/vector_add.py`: example problem plugin

## Quickstart

```bash
uv run kernelswarm run-vector-add --workspace .runs/dev
```

If `uv run` is unavailable in your environment, this equivalent command works:

```bash
PYTHONPATH=src .venv/bin/python -m kernelswarm run-vector-add --workspace .runs/dev
```

Run with real CUDA backend on NVIDIA hosts:

```bash
PYTHONPATH=src .venv/bin/python -m kernelswarm run-vector-add \
  --workspace .runs/nvcc \
  --backend nvcc
```

On fresh Ubuntu GPU instances, ensure both CUDA and host compilers are installed:

```bash
sudo apt-get update
sudo apt-get install -y nvidia-cuda-toolkit gcc g++
```

### Outputs

The run writes:

- run database: `.runs/dev/db/runs.sqlite`
- artifacts: `.runs/dev/artifacts/<run_id>/`
- run report: `.runs/dev/artifacts/<run_id>/reports/run_report.json`

## Smoke Test

```bash
./scripts/smoke_test.sh
```

## Remote Eval Worker (Local Brain, Remote Muscle)

Start worker:

```bash
PYTHONPATH=src .venv/bin/python -m kernelswarm serve-eval-worker --host 127.0.0.1 --port 8080
```

Run pipeline against worker:

```bash
PYTHONPATH=src .venv/bin/python -m kernelswarm run-vector-add \
  --workspace .runs/remote-demo \
  --backend nvcc \
  --remote-eval-url http://127.0.0.1:8080
```

This is the contract you will point at Brev GPU worker instances next.

For Brev smoke testing on a single GPU instance, running both commands on the instance itself is the fastest validation path:

```bash
PYTHONPATH=src uv run python -m kernelswarm serve-eval-worker --host 0.0.0.0 --port 8080
PYTHONPATH=src uv run python -m kernelswarm run-vector-add \
  --workspace .runs/brev-remote-smoke \
  --backend nvcc \
  --remote-eval-url http://127.0.0.1:8080 \
  --top-k-full 1 --seed-count 2 --quick-size 512 --full-size 1024 \
  --quick-iters 2 --full-iters 3 --quick-warmup 1 --full-warmup 1
```

## Swarm Search (MAP-Elites + Nemotron + Brev)

Run iterative 4-island MAP-Elites search with 32 generator and 32 judge logical agents:

```bash
export NVIDIA_API_KEY=...
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search \
  --backend nvcc \
  --remote-eval-url http://127.0.0.1:8080 \
  --nemotron-model nvidia/nemotron-3-nano-30b-a3b \
  --nemotron-base-url https://integrate.api.nvidia.com/v1 \
  --max-iterations 500
```

Offline/local fallback (no LLM calls):

```bash
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search-local \
  --llm-disabled \
  --backend python-sim \
  --max-iterations 100
```

Brev preflight/create from CLI:

```bash
uv run python -m kernelswarm brev-ensure-instance \
  --name kernelswarm-eval \
  --machine n1-highmem-4:nvidia-tesla-t4:1 \
  --create-if-missing
```

`run-swarm-search` also supports Brev preflight directly:

```bash
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search \
  --brev-instance kernelswarm-eval \
  --brev-create-if-missing \
  --remote-eval-url http://127.0.0.1:8080
```
