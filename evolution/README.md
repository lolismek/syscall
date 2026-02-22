# KernelSwarm

This repository contains the v1 implementation foundation for KernelSwarm:

- canonical schemas and deterministic hashing
- `OptimizationProblem` SDK
- sequential evaluation pipeline (`build -> validate -> benchmark -> score -> persist`)
- SQLite persistence and artifact storage
- reproducibility manifest capture
- deep source-mutation DSL (`replace` / `insert_before` / `insert_after` / `append` / `prepend`)
- two-stage judge flow (`triage` + `full_gate`)
- example `vector_add` plugin
- `reduction_v1` plugin
- `stencil2d_v1` plugin
- KernelBench plugin (`kernelbench_v1`)

## Layout

- `src/kernelswarm/models.py`: canonical run/candidate/result/state contracts
- `src/kernelswarm/sdk.py`: problem plugin protocol
- `src/kernelswarm/pipeline.py`: single-worker orchestration
- `src/kernelswarm/persistence.py`: SQLite store
- `src/kernelswarm/manifest.py`: environment + toolchain manifest
- `src/kernelswarm/plugins/vector_add.py`: example problem plugin
- `src/kernelswarm/plugins/reduction.py`: reduction plugin
- `src/kernelswarm/plugins/stencil2d.py`: 2D stencil plugin
- `src/kernelswarm/plugins/kernelbench.py`: KernelBench-backed problem plugin

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

Run a KernelBench problem:

```bash
uv run kernelswarm run-kernelbench \
  --workspace .runs/kernelbench \
  --repo-path /path/to/KernelBench \
  --dataset-source local \
  --level 1 \
  --problem-id 23 \
  --backend cuda \
  --precision fp32
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
export DEEPINFRA_API_KEY=...
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search \
  --backend nvcc \
  --remote-eval-url http://127.0.0.1:8080 \
  --nemotron-provider deepinfra \
  --nemotron-model nvidia/Nemotron-3-Nano-30B-A3B \
  --nemotron-max-concurrent-requests 32 \
  --max-iterations 500
```

NVIDIA API remains supported:

```bash
export NVIDIA_API_KEY=...
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search \
  --nemotron-provider nvidia \
  --nemotron-model nvidia/nemotron-3-nano-30b-a3b \
  --nemotron-base-url https://integrate.api.nvidia.com/v1
```

KernelBench via swarm search:

```bash
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search-kb \
  --problem-id kernelbench_v1 \
  --backend cuda \
  --kb-repo-path /path/to/KernelBench \
  --kb-dataset-source local \
  --kb-level 1 \
  --kb-problem-id 23 \
  --remote-eval-url http://127.0.0.1:8080
```

Offline/local fallback (no LLM calls):

```bash
uv run python -m kernelswarm run-swarm-search \
  --workspace .runs/search-local \
  --llm-disabled \
  --backend python-sim \
  --max-iterations 100
```

Cross-problem suite runner (multiple problems x multiple seeds):

```bash
./scripts/run_multi_problem_suite.sh .runs/suite-v1
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

## Local Dashboard

Serve a local dashboard (API + browser UI) for an existing run workspace:

```bash
uv run python -m kernelswarm serve-dashboard \
  --workspace .runs/search \
  --host 127.0.0.1 \
  --port 8090
```

Open `http://127.0.0.1:8090` to watch:

- global best fitness over iterations
- global best median latency over iterations
- per-island coverage state over time
- live quick/full leaderboards and candidate state counts
