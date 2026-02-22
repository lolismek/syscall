# KernelSwarm v1 Specification

Status: Draft v1.0  
Date: 2026-02-21  
Owners: Core Runtime + Evaluation + Infra

## 1. Purpose

KernelSwarm v1 is a production-oriented system for discovering high-performance GPU kernel/program variants using a 64-agent LLM swarm and MAP-Elites search.

This specification defines hard contracts for v1 so the system can run unattended and be reproducible.

## 2. Hard Constraints

- Agent count: 64 logical agents per run.
- LLM: all agent roles use `nvidia/nemotron-3-nano-30b-a3b` only.
- Runtime platform: NVIDIA Brev instances.
- Domain: optimization-problem agnostic via plugin interface; no PyTorch coupling in core.
- Core loop: propose -> triage -> compile -> validate -> benchmark -> archive.

## 3. V1 Decisions (Locked)

### 3.1 Optimization Scope

- V1 primary candidate language: `cuda_cpp`.
- V1 optional candidate language: `ptx` (plugin opt-in).
- No first-class Triton/CUTLASS search in core v1 (can still be wrapped by plugin as external build flow).
- Objective: single-kernel latency minimization with correctness gating.
- Multi-objective scoring is supported by plugin, but controller uses scalarized fitness for archive replacement in v1.

### 3.2 External Library Policy

- External libraries are allowed (`cuBLAS`, `CUTLASS`, etc.) if plugin permits.
- Results are split into two leaderboard tracks:
  - `from_scratch`
  - `library_assisted`

### 3.3 Brev Topology

Default production topology is two-tier:

- Tier A: LLM inference service instance(s).
- Tier B: orchestrator + evaluation workers instance(s).

Single-instance mode is supported for local/dev only.

### 3.4 Toolchain

- Build backends required in v1: `nvcc`.
- Optional plugin backend: `nvrtc`.
- Each run records toolchain fingerprint (CUDA toolkit, driver, compiler, arch flags).

## 4. Critical Reality Checks (From Current Docs)

These are mandatory design assumptions as of 2026-02-21:

- Nemotron model capability includes long context, but practical endpoint limits vary by serving path. The NVIDIA API reference for this model lists max input/output of 128K tokens. Do not assume 1M in production without endpoint verification.
- Reasoning behavior is controllable (`enable_thinking` in chat templates / `chat_template_kwargs`) and must be used as a cost lever.
- NIM thinking budget control is not supported with SGLang; avoid coupling v1 scheduling to this feature.
- NIM 1.15.5 release notes call out a sustained-load memory issue with a workaround involving KV cache reuse. Pin runtime configuration and validate under load.
- Brev Crusoe storage includes non-persistent ephemeral volume. Build/cache/artifact strategy must account for data loss on stop.

## 5. Non-Goals (V1)

- Full compiler replacement (LLVM-style scheduling, register allocation research).
- Global-optimum guarantees.
- Multi-node distributed scheduling beyond a single Brev cluster domain.
- End-to-end graph optimization as a first-class objective.

## 6. System Architecture

## 6.1 Services

- `llm-gateway`: OpenAI-compatible endpoint to Nemotron server.
- `orchestrator-api`: run lifecycle, configs, controls.
- `scheduler`: role scheduling, queue arbitration, budget control.
- `agent-runtime`: 64 logical sessions (32 generator + 32 judge).
- `eval-worker`: compile/validate/benchmark executors.
- `archive-service`: MAP-Elites state + island migration.
- `postgres`: run metadata, candidate lineage, metrics.
- `redis`: durable queues + rate-limited work dispatch.
- `artifact-store`: binaries/logs/reports/checkpoints.
- `dashboard`: live observability and analysis.

## 6.2 GPU Allocation (Reference for 8-GPU host)

- GPU0-1: LLM inference.
- GPU2-7: evaluation workers.

For two-tier deployment, isolate LLM and evaluation on separate instances to reduce benchmark noise.

## 6.3 Data Plane

1. Scheduler asks generator for mutation patch.
2. Judge triages candidate (risk, compile-worthiness, quick repair).
3. Eval worker builds and validates.
4. If valid, run quick benchmark; top candidates run full benchmark.
5. Archive service computes descriptor bin and updates island archive.
6. Migration service exchanges elites across islands on schedule.

## 7. Canonical Data Contracts

All schemas are versioned. Every persisted object includes:

- `schema_version`
- `run_id`
- `created_at`
- `content_hash` (SHA-256 over canonical JSON serialization)

## 7.1 Candidate

```json
{
  "schema_version": "v1",
  "candidate_id": "uuid",
  "parent_ids": ["uuid"],
  "origin": {
    "island_id": "island-a",
    "agent_id": "gen-07",
    "operation": "mutate|repair|seed"
  },
  "representation": {
    "language": "cuda_cpp|ptx",
    "entrypoints": ["kernel_main"],
    "files": [{"path": "kernel.cu", "content": "..."}],
    "patch": "optional unified diff",
    "params": {
      "tile_m": 64,
      "tile_n": 128,
      "tile_k": 32,
      "unroll": 4,
      "vec_width": 4
    },
    "launch": {
      "grid": ["auto", 1, 1],
      "block": [256, 1, 1],
      "dynamic_smem_bytes": 0,
      "stream_mode": "default"
    },
    "compile": {
      "arch": "sm_90",
      "flags": ["-O3", "--use_fast_math"],
      "defines": {"ENABLE_FOO": "1"}
    }
  },
  "track": "from_scratch|library_assisted",
  "hypothesis": "Expected to reduce global memory transactions by coalescing loads"
}
```

## 7.2 BuildResult

```json
{
  "candidate_id": "uuid",
  "status": "success|failure|timeout|infra_error",
  "build_backend": "nvcc|nvrtc",
  "duration_ms": 0,
  "stderr_digest": "sha256",
  "artifacts": {"fatbin": "path", "ptx": "path"},
  "compiler_metrics": {
    "registers_per_thread": 64,
    "smem_static_bytes": 16384,
    "smem_dynamic_bytes": 0,
    "spill_stores": 0,
    "spill_loads": 0
  },
  "toolchain_fingerprint": {
    "cuda": "12.x",
    "driver": "550.xx",
    "nvcc": "12.x",
    "host_compiler": "gcc/clang version"
  }
}
```

## 7.3 ValidationResult

```json
{
  "candidate_id": "uuid",
  "status": "pass|fail|error|timeout",
  "tests_total": 0,
  "tests_passed": 0,
  "tolerance": {"mode": "rtol_atol|ulp|exact", "rtol": 0.0, "atol": 0.0},
  "max_abs_error": 0.0,
  "max_rel_error": 0.0,
  "failing_cases": [{"case_id": "...", "summary": "..."}]
}
```

## 7.4 BenchmarkResult

```json
{
  "candidate_id": "uuid",
  "stage": "quick|full",
  "status": "success|error|timeout",
  "samples": 0,
  "warmup_iters": 0,
  "timing": {
    "median_us": 0.0,
    "p95_us": 0.0,
    "mean_us": 0.0,
    "stdev_us": 0.0,
    "cov": 0.0
  },
  "env": {
    "gpu_name": "...",
    "sm": "...",
    "power_limit_w": 0,
    "clocks_locked": true,
    "cuda_module_loading": "EAGER|LAZY"
  },
  "profile": {
    "nsight_compute": {"enabled": false, "report_path": null}
  }
}
```

## 7.5 Descriptor

```json
{
  "candidate_id": "uuid",
  "descriptor_name": "default_v1",
  "values": {
    "reg_pressure_bin": 2,
    "smem_bin": 1,
    "occupancy_bin": 3
  }
}
```

Default descriptor bins in v1:

- `reg_pressure_bin`: based on `registers_per_thread`.
- `smem_bin`: based on static+dynamic shared memory per block.
- `occupancy_bin`: estimated active-warps occupancy class.

Plugins can override descriptor mapping.

## 8. Plugin SDK (Problem-Agnostic Core)

```python
class OptimizationProblem(Protocol):
    def problem_id(self) -> str: ...
    def baseline(self) -> Candidate | None: ...
    def seed_candidates(self) -> list[Candidate]: ...

    def static_check(self, candidate: Candidate) -> StaticCheckResult: ...
    def build(self, candidate: Candidate) -> BuildResult: ...
    def validate(self, candidate: Candidate, build: BuildResult) -> ValidationResult: ...
    def benchmark(self, candidate: Candidate, build: BuildResult, stage: str) -> BenchmarkResult: ...
    def score(self, validation: ValidationResult, benchmark: BenchmarkResult) -> float | dict: ...
    def describe(self, candidate: Candidate, build: BuildResult, benchmark: BenchmarkResult) -> Descriptor: ...

    def minimize(self, candidate: Candidate) -> Candidate: ...
    def serialize_candidate(self, candidate: Candidate) -> bytes: ...
```

Required behavior:

- `score` must return deterministic output for same inputs.
- `describe` must map into finite bins or normalized coordinates.
- `build/validate/benchmark` must be pure with respect to candidate + run config.

## 9. Orchestration State Machine

## 9.1 Candidate Lifecycle

- `PROPOSED`
- `TRIAGED`
- `REJECTED_STATIC`
- `QUEUED_BUILD`
- `BUILDING`
- `BUILD_FAILED`
- `QUEUED_VALIDATE`
- `VALIDATING`
- `INVALID`
- `QUEUED_BENCH_QUICK`
- `BENCH_QUICK_DONE`
- `QUEUED_BENCH_FULL`
- `BENCH_FULL_DONE`
- `SCORED`
- `ARCHIVED`
- `DEAD_LETTER`

## 9.2 Retry Policy

- Build infra error: retry once.
- Build compile error: optional single judge-repair pass, then one rebuild.
- Validation fail: no retry.
- Benchmark infra error: retry once.
- Any timeout after max retries: `DEAD_LETTER`.

## 9.3 Idempotency

- Work key = `candidate.content_hash + task_type + stage`.
- Duplicate work items are dropped or deduplicated at queue consumer.

## 9.4 Dead Letter

Store full context for forensic replay:

- prompt snapshot
- candidate payload
- compile/validation logs
- worker runtime metadata

## 10. Agent Swarm Specification

## 10.1 Logical Roles

- 32 `GeneratorAgent` sessions.
- 32 `JudgeAgent` sessions.

Sessions are logical identities over a shared model server, not model replicas.

## 10.2 Task Types

- `propose_candidate`
- `judge_candidate`
- `repair_compile_error`
- `analyze_validation_failure`
- `analyze_benchmark_result`

## 10.3 Output Contract

All agent responses are strict JSON against role schema. Free-form prose is rejected.

Generator response fields:

- `patch`
- `changed_knobs`
- `expected_effect`
- `risk_level`
- `reject` (boolean)

Judge response fields:

- `compile_worthy` (boolean)
- `risk_tags`
- `repair_patch` (optional)
- `priority_score` (0-1)

## 10.4 Reasoning and Token Controls

Two request modes:

- `fast` (default): `enable_thinking=false`, low token cap, patch-only output.
- `deep` (escalation only): `enable_thinking=true`, higher token cap.

Controller switches to deep mode only for:

- repeated compile failures on high-potential lineages
- correctness failures with ambiguous root cause
- top-1% candidates before final full benchmark

## 10.5 Budget Contracts

Per-call limits:

- `max_input_tokens`
- `max_output_tokens`
- `latency_budget_ms`
- `schema_required=true`

Per-run limits:

- total tokens
- max deep-mode calls
- max judge-repair attempts

## 10.6 LLM Serving Requirements

- Serve Nemotron through one OpenAI-compatible endpoint per deployment domain.
- Pin model profile and runtime config in code (no implicit runtime defaults).
- Every request must include:
  - `mode`: `fast|deep`
  - `temperature`
  - `max_tokens`
  - `chat_template_kwargs.enable_thinking`
  - strict response schema enforcement
- Persist per-call token usage and latency for budget accounting.
- Apply bounded concurrency at scheduler layer to prevent evaluator starvation.

Default request settings:

- `fast`: `temperature=0.4`, `max_tokens=800`, `enable_thinking=false`
- `deep`: `temperature=0.2`, `max_tokens=2400`, `enable_thinking=true`

Operational notes:

- Validate endpoint context limits before run start; reject configs that exceed verified caps.
- If using NIM, enable documented KV-cache reuse and pin NIM version in deployment manifest.
- If using SGLang backend, do not depend on thinking-budget-control features.

## 11. MAP-Elites with 4 Islands

## 11.1 Archive Type

Default v1 uses grid archive (low-dimensional descriptors).

- Each island uses identical descriptor definitions but distinct selection/mutation policies.
- If descriptor dimension > 3, use CVT archive.

## 11.2 Island Policies

- Island A: correctness-first (low mutation magnitude).
- Island B: aggressive transformation (higher novelty weight).
- Island C: memory behavior exploration.
- Island D: launch/occupancy exploration.

## 11.3 Parent Selection

Per island selection mix:

- 60% uniform over occupied bins.
- 30% fitness-biased among occupied bins.
- 10% novelty-biased/recently improved bins.

## 11.4 Replacement Rule

Candidate replaces incumbent iff:

- target bin empty, or
- scalarized fitness improves by epsilon threshold.

## 11.5 Migration

- Topology: ring (`A->B->C->D->A`).
- Trigger: every 50 accepted archive updates or every 15 minutes, whichever comes first.
- Packet size: 3 elites per island.
- On stagnation (no global best improvement for 30 minutes), double migration packet size for one cycle.

## 11.6 Checkpointing

Checkpoint every 5 minutes and every 100 accepted updates.

Checkpoint includes:

- all island archives
- scheduler/emitter state
- RNG states
- queue offsets
- lineage graph
- budgets consumed

Resume must be bitwise-deterministic for same seed and environment.

## 12. Evaluation Harness Specification

## 12.1 Build and Runtime Isolation

- Build and execution run in sandboxed containers.
- Network disabled for candidate build/run containers.
- Read-only base image, writable scratch workspace.
- Hard limits:
  - compile timeout
  - run timeout
  - max host RAM
  - max tmp disk

## 12.2 Build Cache

Cache key:

`hash(code, compile_flags, include_set, toolchain_fingerprint, target_arch)`

Cached artifacts:

- intermediate objects
- cubin/fatbin/ptx
- parsed compiler metrics

## 12.3 Correctness Gates

Validation order:

1. deterministic unit cases
2. randomized cases
3. edge cases

Failure artifacts must include:

- reference output digest
- candidate output digest
- first mismatch index + magnitude

## 12.4 Sanitizer Gates

- Mandatory on seed candidates and final top-k: `compute-sanitizer --tool memcheck`.
- Weekly/nightly or budgeted runs include `racecheck`, `initcheck`, `synccheck` on top candidates.

## 12.5 Benchmark Protocol

Benchmark must:

- initialize CUDA context outside measured window
- include warmup iterations
- use CUDA events for timing
- synchronize correctly around measured region
- capture median and p95 latency
- report variability (`stdev`, `cov`)

Noise control:

- optional fixed clocks via `nvidia-smi` in controlled environments
- track thermal/power state
- optionally set `CUDA_MODULE_LOADING=EAGER` for deterministic benchmark windows

Two-stage benchmark:

- `quick`: short run for ranking and culling
- `full`: robust run for top-k from quick stage

## 13. Scheduler and Backpressure

## 13.1 Queue Topics

- `candidate.propose`
- `candidate.judge`
- `candidate.repair`
- `eval.build`
- `eval.validate`
- `eval.bench.quick`
- `eval.bench.full`
- `archive.update`
- `island.migrate`
- `run.control`

## 13.2 Adaptive Scheduling Rules

- If eval backlog > threshold, reduce generator throughput and increase judge triage strictness.
- If compile failure rate spikes, allocate more judge-repair tasks.
- If archive coverage stagnates, raise novelty weight and mutation magnitude.

## 13.3 Admission Control

Candidates are rejected before build if any of:

- static check fail
- judge `compile_worthy=false`
- duplicate hash
- budget exhaustion

## 13.4 Queue Message Envelope

All queue messages use this envelope:

```json
{
  "msg_id": "uuid",
  "run_id": "uuid",
  "candidate_id": "uuid",
  "task_type": "eval.build",
  "attempt": 1,
  "created_at": "ISO-8601",
  "not_before": "ISO-8601|null",
  "priority": 50,
  "idempotency_key": "sha256",
  "payload": {}
}
```

Rules:

- `msg_id` is unique per enqueue event.
- `idempotency_key` is stable for semantically identical work.
- `attempt` increments on retry and drives dead-letter thresholds.
- Consumers ack only after durable state write.

## 14. Storage and Observability

## 14.1 Required Tables (Postgres)

- `runs`
- `candidates`
- `lineage_edges`
- `build_results`
- `validation_results`
- `benchmark_results`
- `descriptors`
- `archive_cells`
- `agent_calls`
- `budget_events`
- `dead_letters`

## 14.2 Artifact Layout

- `artifacts/<run_id>/candidates/<candidate_id>/source/`
- `artifacts/<run_id>/candidates/<candidate_id>/build/`
- `artifacts/<run_id>/candidates/<candidate_id>/validation/`
- `artifacts/<run_id>/candidates/<candidate_id>/benchmark/`
- `artifacts/<run_id>/checkpoints/`
- `artifacts/<run_id>/reports/`

## 14.3 Metrics (Minimum)

- token throughput, queue depth, agent latency
- compile success/fail rate
- validation pass rate
- benchmark throughput and variance
- archive fill ratio per island
- best fitness over time
- cost per accepted improvement

## 15. Brev Deployment Spec

## 15.1 Compose Services

`docker-compose.yml` must define:

- `llm-server`
- `orchestrator`
- `scheduler`
- `eval-worker` (scale)
- `redis`
- `postgres`
- `dashboard`
- `prometheus` (optional but recommended)

## 15.2 Healthchecks

Minimum health probes:

- LLM: `GET /v1/models` or readiness endpoint.
- NIM (if used): `GET /v1/health/ready`.
- Orchestrator: `GET /healthz`.
- Eval worker: startup self-test (`nvcc --version`, CUDA device visible, tiny compile+run).

Startup ordering uses `depends_on` with `service_healthy` for critical dependencies.

## 15.3 Storage on Brev

- Use ephemeral volume for high-churn cache/benchmark scratch.
- Sync important artifacts and checkpoints to persistent object storage periodically.
- Never rely on local ephemeral storage for run recovery.

## 15.4 Network and Access

- Internal service communication over private network.
- Public exposure only for dashboard and optional API.
- Prefer SSH port-forwarding for admin endpoints over broad public tunnels.

## 15.5 Brev Bootstrap Requirements

Repository ships with:

- `docker-compose.yml`
- `.env.example`
- `scripts/bootstrap_brev.sh`
- `scripts/smoke_test.sh`

Bootstrap script must:

- validate NVIDIA driver/CUDA visibility
- validate compose health status
- run a tiny compile+validate+benchmark smoke job

Smoke test failure blocks run startup.

## 16. Security and Safety

- No arbitrary host command execution from LLM outputs.
- Candidate code can only run through controlled evaluator entrypoint.
- Enforce explicit allowlist for compilers/tools and include paths.
- Redact secrets from all persisted prompts/logs.
- Audit log every agent decision that triggers GPU benchmark spend.

## 17. Acceptance Criteria (V1 Exit)

A build is considered v1-complete when all are true:

- 64 logical agent sessions run concurrently for 2+ hours without orchestrator failure.
- At least one non-trivial plugin can run end-to-end without core code changes.
- Full resume from checkpoint works after forced restart.
- Re-running same seed and config reproduces identical archive and top-k ordering (within benchmark noise tolerance).
- Budget enforcement blocks overspend deterministically.
- Two-tier Brev deployment can be brought up from clean instance with documented steps.
- Generated report includes correctness, benchmark, archive coverage, and cost metrics.

## 18. Implementation Plan (Execution Order)

1. Core schemas + hashing + persistence.
2. Plugin SDK + single-problem harness.
3. Eval worker pipeline with cache and reproducible benchmark protocol.
4. Orchestrator state machine + queues + retry/dead-letter.
5. Nemotron serving integration and agent runtime.
6. MAP-Elites islands + migration + checkpoints.
7. Cost controls + adaptive scheduler.
8. Brev packaging + smoke tests + observability dashboards.

## 19. Source Notes (Validated During Drafting)

Primary references used for v1 assumptions:

- Nemotron API reference: `https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-nano-30b-a3b`
- NIM configuration: `https://docs.nvidia.com/nim/large-language-models/latest/configuration.html`
- NIM thinking budget control: `https://docs.nvidia.com/nim/large-language-models/latest/thinking-budget-control.html`
- NIM release notes: `https://docs.nvidia.com/nim/large-language-models/latest/release-notes.html`
- Brev docs index: `https://docs.nvidia.com/brev/latest/`
- Brev custom containers/compose mode: `https://docs.nvidia.com/brev/latest/custom-containers.html`
- Brev launchables: `https://docs.nvidia.com/brev/latest/launchables.html`
- Brev Crusoe storage: `https://docs.nvidia.com/brev/latest/crusoe-instances.html`
- Brev deployments: `https://docs.nvidia.com/brev/latest/deployments.html`
- CUDA lazy loading guidance: `https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/lazy-loading.html`
- Nsight Compute profiling guidance: `https://docs.nvidia.com/nsight-compute/`
- Compute Sanitizer docs: `https://docs.nvidia.com/compute-sanitizer/ComputeSanitizer/index.html`
- NVIDIA SMI reference: `https://docs.nvidia.com/deploy/nvidia-smi/`
- MAP-Elites paper: `https://arxiv.org/abs/1504.04909`
- CVT-MAP-Elites paper: `https://arxiv.org/abs/1610.05729`
- pyribs docs: `https://docs.pyribs.org/`
