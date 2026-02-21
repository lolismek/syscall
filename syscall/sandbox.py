"""Subprocess-based code execution and timing."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from syscall.models import Problem, ScoredSubmission, SubmissionMessage, TestCase

_HARNESS_SUFFIX = """
import sys, json
data = json.loads(sys.stdin.read())
result = solve(*data["args"])
print(json.dumps(result))
"""


def _run_single_test(code: str, test_case: TestCase, timeout: float) -> tuple[bool, float]:
    """Run code against a single test case. Returns (passed, elapsed_ms)."""
    full_code = code + "\n" + _HARNESS_SUFFIX

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(full_code)
        tmp_path = f.name

    try:
        start = time.perf_counter()
        result = subprocess.run(
            ["python3", tmp_path],
            input=test_case.input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        if result.returncode != 0:
            return False, elapsed_ms

        actual = result.stdout.strip()
        expected = test_case.expected_output.strip()

        # Compare as JSON to handle formatting differences
        try:
            passed = json.loads(actual) == json.loads(expected)
        except json.JSONDecodeError:
            passed = actual == expected

        return passed, elapsed_ms

    except subprocess.TimeoutExpired:
        return False, timeout * 1000
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _score_sync(msg: SubmissionMessage, problem: Problem) -> ScoredSubmission:
    """Synchronously score a submission against all test cases."""
    all_tests = problem.test_cases + problem.hidden_test_cases
    total_ms = 0.0
    passed_all = True

    for tc in all_tests:
        passed, elapsed = _run_single_test(msg.code, tc, problem.timeout_seconds)
        total_ms += elapsed
        if not passed:
            passed_all = False
            break

    return ScoredSubmission(
        agent_id=msg.agent_id,
        code=msg.code,
        generation=msg.generation,
        execution_time_ms=round(total_ms, 2),
        passed_all=passed_all,
        timestamp=datetime.now(timezone.utc),
    )


async def score_submission(msg: SubmissionMessage, problem: Problem) -> ScoredSubmission:
    """Score a submission without blocking the event loop."""
    return await asyncio.to_thread(_score_sync, msg, problem)
