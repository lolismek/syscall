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

### Outputs

The run writes:

- run database: `.runs/dev/db/runs.sqlite`
- artifacts: `.runs/dev/artifacts/<run_id>/`
- run report: `.runs/dev/artifacts/<run_id>/reports/run_report.json`

## Smoke Test

```bash
./scripts/smoke_test.sh
```
