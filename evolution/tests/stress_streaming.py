"""Stress test: 32 concurrent streaming LLM calls through NemotronClient."""
from __future__ import annotations

import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kernelswarm.nemotron import NemotronClient, NemotronConfig, FAST_MODE

NUM_WORKERS = 32

# Realistic-ish prompt — not huge, but enough to exercise streaming
SYSTEM_PROMPT = "You are a code optimization assistant. Return JSON only."
USER_PROMPT = """\
Given this Triton kernel for LayerNorm, suggest one optimization.
Return JSON: {"reject": false, "params_patch": {}, "launch_patch": {}}

```python
import triton
import triton.language as tl

@triton.jit
def layer_norm_kernel(X, Y, W, B, Mean, Rstd, stride, N, eps, BLOCK_SIZE: tl.constexpr):
    row = tl.program_id(0)
    cols = tl.arange(0, BLOCK_SIZE)
    mask = cols < N
    x = tl.load(X + row * stride + cols, mask=mask, other=0.0).to(tl.float32)
    mean = tl.sum(x, axis=0) / N
    xmean = x - mean
    var = tl.sum(xmean * xmean, axis=0) / N
    rstd = 1.0 / tl.sqrt(var + eps)
    y = xmean * rstd
    w = tl.load(W + cols, mask=mask, other=1.0).to(tl.float32)
    b = tl.load(B + cols, mask=mask, other=0.0).to(tl.float32)
    y = y * w + b
    tl.store(Y + row * stride + cols, y.to(tl.float16), mask=mask)
```
"""

config = NemotronConfig(
    provider=os.environ.get("KERNELSWARM_NEMOTRON_PROVIDER", "deepinfra"),
    model=os.environ.get("KERNELSWARM_NEMOTRON_MODEL", ""),
    api_key=os.environ.get("DEEPINFRA_API_KEY", ""),
    max_concurrent_requests=NUM_WORKERS,
)
client = NemotronClient(config)

results: dict[int, str] = {}
errors: dict[int, str] = {}
latencies: dict[int, float] = {}
lock = threading.Lock()


def worker(worker_id: int) -> None:
    t0 = time.perf_counter()
    try:
        result = client.chat_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT,
            mode=FAST_MODE,
        )
        elapsed = time.perf_counter() - t0
        with lock:
            results[worker_id] = f"OK {result.usage.completion_tokens}tok {elapsed:.1f}s"
            latencies[worker_id] = elapsed
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        with lock:
            errors[worker_id] = f"{type(exc).__name__}: {exc} ({elapsed:.1f}s)"


print(f"Launching {NUM_WORKERS} concurrent streaming requests to {config.model} via {config.provider}...")
t_start = time.perf_counter()

with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
    futures = [pool.submit(worker, i) for i in range(NUM_WORKERS)]
    for f in as_completed(futures):
        f.result()  # propagate unexpected exceptions

t_total = time.perf_counter() - t_start

print(f"\n{'='*60}")
print(f"Results: {len(results)} OK, {len(errors)} FAILED  ({t_total:.1f}s total)")
print(f"{'='*60}")

if latencies:
    lats = sorted(latencies.values())
    print(f"Latency: min={lats[0]:.1f}s  median={lats[len(lats)//2]:.1f}s  max={lats[-1]:.1f}s")

if errors:
    print(f"\nFAILED ({len(errors)}):")
    for wid in sorted(errors):
        print(f"  worker {wid}: {errors[wid]}")

if results:
    print(f"\nSUCCESS ({len(results)}):")
    for wid in sorted(results):
        print(f"  worker {wid}: {results[wid]}")
