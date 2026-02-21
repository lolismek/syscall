# Session Handoff: KernelSwarm Debugging & Next Steps

## What We Did This Session

### 1. Diagnosed 97% LLM failure rate
The run was showing 860 `generator_rejected` vs 25 successes. Root cause chain:
- `agents.py:312` had a bare `except Exception` that silently swallowed all LLM errors
- Added logging to `agents.py` (logger at module level) and `logging.basicConfig` in `cli.py:main()`
- Discovered Nemotron-3-Nano returns `reasoning_content` but empty `content` — the model was thinking by default and spending all tokens on chain-of-thought
- DeepInfra DOES support `chat_template_kwargs: {enable_thinking: false}` — the code had `_supports_chat_template_kwargs` returning `False` for deepinfra. Fixed to return `True`
- Also enabled `_supports_json_response_format` for deepinfra (was `False`, now `True`)

### 2. Fixed concurrency issues
- 32 concurrent requests to DeepInfra causes "Remote end closed connection" drops
- Added retry logic in `nemotron.py:chat_json` — 3 attempts with jittered backoff
- Reduced workers to 10 in `scripts/run_medium_20m.sh` (generators, judges, proposal workers, max concurrent)
- Bumped default timeout from 60s to 180s in `nemotron.py`

### 3. Swapped model from Nemotron to DeepSeek V3.2
- `nemotron.py:DEFAULT_NEMOTRON_MODEL` changed from `nvidia/Nemotron-3-Nano-30B-A3B` to `deepseek-ai/DeepSeek-V3.2`
- All code still uses "nemotron" naming (client, config, CLI flags) — this is intentional for easy switching back
- DeepSeek V3.2 is $0.26/$0.38 per M tokens on DeepInfra, 671B MoE model
- Nemotron Nano (3.5B active params) was fundamentally too small for kernel generation — no one has used it for this in any published work

### 4. Fixed prompt issues
**Problem:** The system prompt told the LLM "do NOT delegate to Model, do NOT inherit from Model" — this explicitly blocked `torch.compile(Model(...))` which was the 4.4x seed strategy. The LLM would see the torch.compile parent and rewrite it from scratch as manual PyTorch.

**Fix in `agents.py`:**
- Removed the anti-delegation rules from `_KB_SYSTEM_PROMPT`
- Added to `_KB_USER_TEMPLATE`: instruction to preserve torch.compile when seen in parent
- Listed all valid approaches (torch.compile, CUDA, Triton, optimized PyTorch)

### 5. Added parameter integrity check
**Problem:** Candidates that dropped `weight`/`bias` params scored 2.9x because KernelBench only tests with default-initialized params (weight=1, bias=0 = identity transform).

**Fix in `plugins/kernelbench.py:static_check`:** Added check that candidates must have `nn.Parameter`, parameterized modules (nn.LayerNorm, nn.Linear, etc.), or delegate to Model. Candidates without learnable params are rejected at static check.

### 6. Improved JSON parsing
`nemotron.py:_extract_json_payload` was fragile. Replaced with:
1. Try `json.loads(full_content)`
2. Try `json.JSONDecoder().raw_decode()` from first `{`
3. Fallback: progressively shorter slices from last `}`

### 7. Eval worker maintenance
- Cleared stale torch inductor cache: `rm -rf /tmp/torchinductor_shadeform`
- Restart command:
```bash
ssh kernel-swarm-eval-new-2 bash <<'EOF'
pkill -f serve-eval-worker 2>/dev/null
sleep 2
rm -rf /tmp/torchinductor_shadeform 2>/dev/null
tmux kill-session -t eval 2>/dev/null
tmux new-session -d -s eval 'cd /home/shadeform/syscall/evolution && PYTHONPATH=src .venv/bin/python -m kernelswarm serve-eval-worker --host 0.0.0.0 --port 8080 > /tmp/eval-worker.log 2>&1'
sleep 5
curl -sf http://127.0.0.1:8080/healthz
EOF
```

### 8. Created monitoring script
`scripts/check_run.sh` — run with no args for latest run status. Shows iteration breakdown, quick eval distribution, full leaderboard, LLM stats.

---

## Current State (end of session)

### What's working
- DeepSeek V3.2 generating candidates, ~10 workers, zero connection rejections
- Candidates have proper learnable parameters (static check catches cheaters)
- torch.compile seed at 4.6x is the leader
- DeepSeek is writing **real Triton kernels** with proper structure

### The blocking issue: Triton kernels can't run in eval harness

**Every Triton kernel fails with:** `@jit functions should be defined in a Python file`

**Root cause:** KernelBench evaluates candidates by `exec()`ing the source code as a string. Triton's `@triton.jit` decorator needs to read the function's source via `inspect.getsource()`, which requires the code to exist in an actual `.py` file on disk. Dynamically exec'd code has no file backing, so Triton fails.

**Evidence:** `ssh kernel-swarm-eval-new-2 'grep "jit functions" /tmp/eval-worker.log'` shows this error for every Triton candidate.

**Impact:** DeepSeek V3.2 is producing well-structured Triton kernels (proper masking, mean/variance reductions, weight/bias handling) but none can be evaluated. This is the single biggest thing blocking better results.

**Fix needed:** Modify how KernelBench loads candidate source code — write it to a temporary `.py` file and import it as a module instead of exec(). The eval happens in `plugins/kernelbench.py:_evaluate_kernel` which calls `kb_eval.eval_kernel_against_ref()`. The actual exec happens inside the KernelBench library on the remote at `/home/shadeform/KernelBench`. You'll need to find where KernelBench does `exec()` on candidate source and change it to write-to-file + import.

**Quick check:** `ssh kernel-swarm-eval-new-2 'grep -r "exec(" /home/shadeform/KernelBench/src/ | head -20'`

### Other observations
- Migration (torch.compile spreading across islands) hasn't triggered — needs 50 accepted archive updates, current throughput is too low
- Best LLM-generated candidate without Triton: 1.6x speedup (manual mean/var in PyTorch)
- `jaberjaber2` / RightNow AI "Forge" post that inspired this project was mostly startup marketing with unverified claims — the approach is legit but results were inflated

---

## Key Files Modified

| File | What changed |
|------|-------------|
| `src/kernelswarm/nemotron.py` | Model default, retry logic, timeout, logging, JSON parsing, enabled chat_template_kwargs + json response_format for deepinfra |
| `src/kernelswarm/agents.py` | Logging on all error paths, prompt rewrite (allow torch.compile, preserve parent approach), enable_thinking=False with 4096 tokens |
| `src/kernelswarm/plugins/kernelbench.py` | Parameter integrity static check |
| `src/kernelswarm/cli.py` | `logging.basicConfig` in `main()` |
| `scripts/run_medium_20m.sh` | Workers reduced to 10 |
| `scripts/check_run.sh` | New monitoring script |

## Research Findings

### Best models for kernel generation (from web research)
1. **DeepSeek-R1** — best open model with iterative refinement (36% → 72% on KernelBench)
2. **DeepSeek-V3.2** — currently in use, $0.26/$0.38 per M tokens
3. **Qwen3-Coder-480B** — $0.22/$1.00, strong agentic coding
4. **Dr. Kernel-14B** — competitive with Claude 4.5 Sonnet on KernelBench, uses multi-turn RL
5. **TritonRL (8B)** — state-of-the-art on Triton specifically

### Key techniques from literature
- **Iterative refinement with execution feedback** is the #1 improvement (not yet implemented)
- **Feed compilation errors and profiling data** back to the LLM for next mutation
- **Separate correctness from performance** — first get it to compile, then optimize
- **Multi-model ensemble** — best-of-N across model families >> best-of-N from single model
- **EvoEngineer finding:** traditional crossover/mutation doesn't work with LLMs — decompose into strategy selection + application instead

### References
- [KernelBench leaderboard](https://scalingintelligence.stanford.edu/KernelBenchLeaderboard/)
- [EvoEngineer (arxiv)](https://arxiv.org/abs/2510.03760)
- [AlphaEvolve (DeepMind)](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
- [TritonRL](https://arxiv.org/abs/2510.17891)
- [Dr. Kernel](https://arxiv.org/abs/2602.05885)
- [KernelBook dataset](https://huggingface.co/datasets/GPUMODE/KernelBook)
