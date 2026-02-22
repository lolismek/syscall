# Monitoring KernelSwarm Runs

## Architecture Overview

KernelSwarm evolves GPU kernels using LLM-generated mutations evaluated on a remote GPU box. The local machine runs the search loop and LLM calls. The remote Brev box (NVIDIA L40S) compiles and benchmarks candidates.

```
Local machine                          Brev box (kernel-swarm-eval-new-2)
+--------------------------+           +---------------------------+
| Search loop (search.py)  |           | Eval worker (port 8080)   |
| 32 proposal workers      |  SSH      | KernelBench eval          |
|   -> LLM (DeepInfra)     |  tunnel   | CUDA compilation          |
| 16 quick eval workers    | --------> | Correctness + perf timing |
| 6 full eval workers      |           | NVIDIA L40S GPU           |
| Dashboard (port 8090)    |           |                           |
+--------------------------+           +---------------------------+
```

## Current Problem: KernelBench Level 1, Problem 40 (LayerNorm)

The reference model is a simple `nn.LayerNorm`. Candidates must define `class ModelNew(nn.Module)` that produces identical outputs but runs faster. Optimization approaches include fused CUDA kernels, Triton kernels, torch.compile, and optimized PyTorch operations.

## Starting a Run

```bash
# From evolution/ directory
./scripts/run_medium_20m.sh
```

This starts a 20-minute run with 5000 max iterations and 2M token budget. It auto-establishes an SSH tunnel to the Brev eval worker.

## Dashboard

```bash
# In a separate terminal
./scripts/serve_dashboard.sh .runs/search-medium-20m
```

Opens at http://127.0.0.1:8090. Shows KPI bar, fitness/latency/token charts, leaderboards (quick + full), island grid, and leader kernel source code. Auto-refreshes every 2 seconds.

## Remote Eval Worker

The eval worker runs in a tmux session on the Brev box:

```bash
# Check status
ssh kernel-swarm-eval-new-2 'curl -s http://127.0.0.1:8080/healthz'

# View logs
ssh kernel-swarm-eval-new-2 'tail -50 /tmp/eval-worker.log'

# Attach to tmux session
ssh kernel-swarm-eval-new-2 -t 'tmux attach -t eval'

# Restart eval worker
ssh kernel-swarm-eval-new-2 bash <<'EOF'
pkill -f serve-eval-worker 2>/dev/null
sleep 2
tmux kill-session -t eval 2>/dev/null
tmux new-session -d -s eval 'cd /home/shadeform/syscall/evolution && PYTHONPATH=src .venv/bin/python -m kernelswarm serve-eval-worker --host 0.0.0.0 --port 8080 > /tmp/eval-worker.log 2>&1'
sleep 3
curl -sf http://127.0.0.1:8080/healthz
EOF
```

## Syncing Code to Remote

```bash
# Push local changes to GitHub
git push origin theo-evolution

# Pull on remote
ssh kernel-swarm-eval-new-2 'cd ~/syscall && git pull origin theo-evolution'
```

The remote uses `~/syscall/evolution/` with a symlink at `~/kernelswarm -> ~/syscall/evolution`. The venv is symlinked from `~/kernelswarm.old/.venv` (which has torch + kernelbench installed). After pulling, restart the eval worker.

## Querying the Database

Run data is in `.runs/search-medium-20m/db/runs.sqlite`.

```bash
# Get latest run ID
sqlite3 .runs/search-medium-20m/db/runs.sqlite \
  "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1;"

# Candidate outcome breakdown (substitute RUN_ID)
sqlite3 .runs/search-medium-20m/db/runs.sqlite "
SELECT json_extract(s.payload_json, '$.raw_score.reason') as reason, count(*)
FROM scores s WHERE s.run_id = 'RUN_ID' AND s.stage = 'quick'
GROUP BY reason ORDER BY count(*) DESC;
"

# Top full leaderboard
sqlite3 .runs/search-medium-20m/db/runs.sqlite "
SELECT c.candidate_id, s.scalar_fitness,
       json_extract(s.payload_json, '$.raw_score.speedup_vs_ref') as speedup,
       substr(json_extract(c.payload_json, '$.hypothesis'), 1, 100) as hyp
FROM scores s JOIN candidates c ON s.candidate_id = c.candidate_id
WHERE s.run_id = 'RUN_ID' AND s.stage = 'full'
ORDER BY s.scalar_fitness DESC LIMIT 10;
"

# Duplicate rate (should be <50% after diversity fixes)
sqlite3 .runs/search-medium-20m/db/runs.sqlite "
SELECT json_extract(payload_json, '$.payload.reason') as reason, count(*)
FROM iteration_metrics
WHERE run_id = 'RUN_ID' AND island_id = 'island-a'
GROUP BY reason ORDER BY count(*) DESC;
"

# View a candidate's kernel source
sqlite3 .runs/search-medium-20m/db/runs.sqlite "
SELECT json_extract(c.payload_json, '$.representation.files')
FROM candidates c WHERE c.candidate_id = 'CANDIDATE_ID';
" | python3 -c "import sys,json; f=json.loads(sys.stdin.read()); print(f[0]['content'])"

# Check LLM latency distribution
find .runs/search-medium-20m/artifacts/RUN_ID -name 'generator_decision.json' -print0 | \
  xargs -0 python3 -c "
import sys, json
lats = []
for f in sys.argv[1:]:
    try:
        u = json.load(open(f)).get('usage', {})
        if u.get('latency_ms'): lats.append(u['latency_ms'])
    except: pass
if lats:
    lats.sort()
    print(f'n={len(lats)} avg={sum(lats)//len(lats)}ms p50={lats[len(lats)//2]}ms p90={lats[int(len(lats)*0.9)]}ms max={max(lats)}ms')
"
```

## Schema Reference

```
runs:           run_id, problem_id, status, created_at, manifest_json, config_json, summary_json
candidates:     candidate_id (PK), run_id, content_hash, state, created_at, payload_json
scores:         (run_id, candidate_id, stage) PK, scalar_fitness, payload_json
build_results:  (run_id, candidate_id) PK, payload_json
iteration_metrics: run_id, iteration, island_id, candidate_id, quick_fitness, full_fitness, ...
```

Candidate `payload_json` contains: `representation.files[].content` (source), `hypothesis`, `origin.island_id`, `origin.agent_id`, `origin.operation`.

Score `payload_json` contains: `raw_score.fitness`, `raw_score.median_us`, `raw_score.speedup_vs_ref`, `raw_score.valid`.

## Key Metrics to Watch

| Metric | Healthy | Problem |
|--------|---------|---------|
| Baseline fitness | ~165 (6ms LayerNorm) | -1e18 = eval worker broken |
| Duplicate rate | <50% | >80% = LLM not diverse enough |
| Build failures | <30% | >80% = LLM generating bad code |
| Judge rejections | 0% (bypassed for KB) | >0% = judge not bypassed |
| Speedup vs ref | >1.0x = winning | n/a = reference runtime broken |
| LLM latency | 10-30s | >60s = API issues |

## Evolution Strategy

4 islands with MAP-Elites archives, each with a different optimization style:
- **island-a** `correctness_first` (scale 0.6): torch.compile, simple fusions
- **island-b** `aggressive` (scale 1.4): custom CUDA kernels, shared memory, full rewrites
- **island-c** `memory_explorer` (scale 1.0): memory access patterns, tiling, coalescing
- **island-d** `occupancy_tuner` (scale 0.9): block sizes, register pressure, warp occupancy

Migration every 50 accepted updates: top 3 elites flow a→b→c→d→a.

Parent selection: 60% random cell, 30% fitness-biased, 10% novelty-biased.

The LLM sees the reference source + parent source + island style guidance + diversity seed, and returns a complete `ModelNew` implementation. This is a full file rewrite, not a patch.

## Concurrency Settings

| Setting | Value | Notes |
|---------|-------|-------|
| Proposal workers | 32 | Parallel LLM calls |
| Nemotron max concurrent | 32 | DeepInfra API limit |
| Quick eval workers | 16 | Remote eval parallelism |
| Full eval workers | 6 | Expensive, fewer slots |
| Max inflight proposals | 128 | Queue depth |
| Max inflight quick evals | 48 | Queue depth |
| Max inflight full evals | 16 | Queue depth |
| LLM token rate limit | 5000 tokens/s | DeepInfra cap |
| LLM max output tokens | 4096 | Per candidate |
| LLM temperature | 0.9 | High for diversity |

## Common Issues

**All builds failing with `FileNotFoundError`**: The `--kb-repo-path` sent to the remote doesn't match where KernelBench is installed. Should be `/home/shadeform/KernelBench`. Check `scripts/run_medium_20m.sh` `resolve_remote_kb_repo_path()`.

**Speedup showing n/a**: The reference runtime benchmark failed. SSH into Brev box, check `/tmp/eval-worker.log`. Common cause: the `Model` → `ModelNew` rename in `_reference_runtime_stats` isn't working. Verify with a direct test on the remote.

**Eval worker unreachable**: SSH tunnel may have died. The run script auto-tunnels; restart the run. Or manually: `ssh -N -L 18080:127.0.0.1:8080 kernel-swarm-eval-new-2`.

**87% duplicate rate**: Fixed by temperature=0.9 and diversity seed in prompt. If still high, the population has converged and the LLM can't find new variations — consider restarting with different seeds or a harder problem.

**Judge rejecting valid candidates**: Should not happen anymore — the judge is bypassed for KernelBench (`kernelbench auto-allow`). If you see judge rejections, `prompt_context` is not being threaded through correctly.
