"""Agent worker: connects to server, calls LLM, validates, submits solutions."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
log = logging.getLogger("syscall.worker")

_HARNESS_SUFFIX = """
import sys, json
data = json.loads(sys.stdin.read())
result = solve(*data["args"])
print(json.dumps(result))
"""


def extract_code(text: str) -> str:
    """Extract Python code from markdown code blocks, or return raw text."""
    pattern = r"```(?:python)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    return text.strip()


def validate_locally(code: str, test_cases: list[dict], timeout: float = 10.0) -> tuple[bool, str | None]:
    """Run code against public test cases locally. Returns (passed, error_detail)."""
    full_code = code + "\n" + _HARNESS_SUFFIX
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(full_code)
        tmp_path = f.name

    try:
        for i, tc in enumerate(test_cases):
            try:
                result = subprocess.run(
                    [sys.executable, tmp_path],
                    input=tc["input"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if result.returncode != 0:
                    err = result.stderr[:500]
                    log.warning(f"  Local validation failed (exit {result.returncode}): {err[:200]}")
                    return False, f"Runtime error on test case {i+1}: {err}"
                actual = result.stdout.strip()
                expected = tc["expected_output"].strip()
                if json.loads(actual) != json.loads(expected):
                    log.warning(f"  Local validation wrong answer: got {actual}, expected {expected}")
                    return False, f"Wrong answer on test case {i+1}: input={tc['input'][:200]}, expected={expected}, got={actual}"
            except subprocess.TimeoutExpired:
                log.warning("  Local validation timed out")
                return False, f"Timeout on test case {i+1} (>{timeout}s)"
            except Exception as e:
                log.warning(f"  Local validation error: {e}")
                return False, f"Execution error on test case {i+1}: {e}"
        return True, None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def build_generate_prompt(problem: dict) -> str:
    return f"""You are an expert competitive programmer. Solve this problem with the most efficient algorithm possible.

**Problem:** {problem["title"]}
{problem["description"]}

**Function signature:** `{problem["function_signature"]}`

**Requirements:**
- Define a function called `solve` with the exact signature above
- Optimize for speed — your solution will be timed on large inputs
- Do NOT include any imports unless absolutely necessary
- Do NOT include test code or print statements outside the function
- Return ONLY the function definition in a Python code block

```python
{problem["function_signature"]}
    # your code here
```"""


def build_evolve_prompt(problem: dict, top_solutions: list[dict]) -> str:
    solutions_text = ""
    for i, sol in enumerate(top_solutions):
        solutions_text += f"\n### Solution {i+1} (agent: {sol['agent_id']}, time: {sol['execution_time_ms']:.1f}ms)\n```python\n{sol['code']}\n```\n"

    return f"""You are an expert competitive programmer. Your goal is to write the FASTEST possible solution.

**Problem:** {problem["title"]}
{problem["description"]}

**Function signature:** `{problem["function_signature"]}`

Here are the current top solutions and their execution times:
{solutions_text}

**Your task:** Write a solution that is FASTER than all of the above. Consider:
- Better algorithms (lower time complexity)
- Micro-optimizations (avoid unnecessary allocations, use built-in functions)
- Avoid redundant work

**Requirements:**
- Define a function called `solve` with the exact signature above
- Do NOT include any imports unless absolutely necessary
- Do NOT include test code or print statements outside the function
- Return ONLY the function definition in a Python code block

```python
{problem["function_signature"]}
    # your optimized code here
```"""


def build_reflection_prompt(problem: dict, code: str, error: str) -> str:
    return f"""You are an expert competitive programmer. Your previous solution has a bug. Fix it.

**Problem:** {problem["title"]}
{problem["description"]}

**Function signature:** `{problem["function_signature"]}`

**Your previous code:**
```python
{code}
```

**Error:**
{error}

Analyze the error, identify the root cause, and write a corrected solution.

**Requirements:**
- Define a function called `solve` with the exact signature above
- Do NOT include any imports unless absolutely necessary
- Do NOT include test code or print statements outside the function
- Return ONLY the corrected function definition in a Python code block

```python
{problem["function_signature"]}
    # your corrected code here
```"""


async def call_llm(api_url: str, model: str, api_key: str | None, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2048,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        url = f"{api_url.rstrip('/')}/chat/completions"
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def run_worker(server_url: str, api_url: str, model: str, api_key: str | None):
    import websockets

    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    log.info(f"Starting worker {agent_id}, model={model}")
    log.info(f"Server: {server_url}, LLM API: {api_url}")

    async with websockets.connect(server_url) as ws:
        # Register
        await ws.send(json.dumps({
            "type": "register",
            "agent_id": agent_id,
            "model_name": model,
        }))
        log.info("Registered with server")

        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg["type"]

            if msg_type in ("generate", "evolve"):
                generation = msg["generation"]
                problem = msg["problem"]
                log.info(f"Received {msg_type} for generation {generation}")

                if msg_type == "generate":
                    prompt = build_generate_prompt(problem)
                else:
                    prompt = build_evolve_prompt(problem, msg["top_solutions"])

                test_cases = [tc.dict() if hasattr(tc, "dict") else tc for tc in problem["test_cases"]]
                last_code = None
                last_error = None

                # Retry up to 3 times with self-reflection
                for attempt in range(3):
                    try:
                        if attempt > 0 and last_code and last_error:
                            log.info(f"  Attempt {attempt + 1}: reflecting on error...")
                            prompt = build_reflection_prompt(problem, last_code, last_error)
                        else:
                            log.info(f"  Attempt {attempt + 1}: calling LLM...")

                        response = await call_llm(api_url, model, api_key, prompt)
                        code = extract_code(response)

                        if "def solve" not in code:
                            log.warning("  LLM response missing solve function, retrying...")
                            last_code = code
                            last_error = "Your response did not contain a valid `def solve(...)` function definition."
                            continue

                        log.info("  Validating locally...")
                        passed, error = validate_locally(code, test_cases)
                        if passed:
                            log.info("  Local validation passed! Submitting...")
                            await ws.send(json.dumps({
                                "type": "submission",
                                "agent_id": agent_id,
                                "code": code,
                                "generation": generation,
                            }))
                            break
                        else:
                            log.warning(f"  Local validation failed (attempt {attempt + 1}): {error}")
                            last_code = code
                            last_error = error

                    except Exception as e:
                        log.error(f"  Attempt {attempt + 1} error: {e}")
                        last_code = None
                        last_error = None

                else:
                    log.warning(f"  All attempts failed for generation {generation}")

            elif msg_type == "stop":
                best = msg.get("best_solution")
                if best:
                    log.info(f"Evolution stopped. Best: {best['execution_time_ms']:.1f}ms by {best['agent_id']}")
                else:
                    log.info("Evolution stopped. No passing solutions.")
                break


def main():
    parser = argparse.ArgumentParser(description="syscall worker agent")
    parser.add_argument("--server-url", default="ws://localhost:8000/ws/agent", help="WebSocket server URL")
    parser.add_argument("--api-url", default="http://localhost:8000/v1", help="OpenAI-compatible API URL")
    parser.add_argument("--model", default="nvidia/nemotron-ultra-253b", help="Model name")
    parser.add_argument("--api-key", default=None, help="API key (or set LLM_API_KEY env var)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("LLM_API_KEY")

    try:
        asyncio.run(run_worker(args.server_url, args.api_url, args.model, api_key))
    except KeyboardInterrupt:
        log.info("Worker stopped")
    except Exception as e:
        log.error(f"Worker failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
