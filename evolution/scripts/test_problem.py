#!/usr/bin/env python3
"""Sanity-check a YAML problem file against a running eval worker.

Submits the reference implementation (Model → ModelNew) as a candidate,
verifying that:
  1. The eval worker can parse the problem config
  2. The reference builds and passes validation
  3. A reference latency is generated (speedup ≈ 1.0x)

Optionally, submit a custom kernel file instead of the reference.

Usage:
    # Test reference impl against eval worker:
    python scripts/test_problem.py problems/selective_scan.yaml

    # Test a custom kernel file:
    python scripts/test_problem.py problems/selective_scan.yaml --kernel path/to/kernel.py

    # Against a remote eval worker:
    python scripts/test_problem.py problems/selective_scan.yaml --url http://host:8080

    # Full stage (more perf trials):
    python scripts/test_problem.py problems/selective_scan.yaml --stage full
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kernelswarm.models import BenchmarkStage
from kernelswarm.plugins.yaml_problem import YamlProblem
from kernelswarm.remote import RemoteEvaluatorClient, RemoteEvaluationError
from kernelswarm.sdk import ProblemRunContext
from kernelswarm.serialization import to_dict
from kernelswarm.hashing import attach_content_hashes


def load_candidate_source(problem: YamlProblem, kernel_path: str | None) -> str:
    """Return candidate source code: either from a file or the reference with Model→ModelNew."""
    if kernel_path:
        return Path(kernel_path).read_text(encoding="utf-8")
    # Use the reference as the candidate (should get speedup ≈ 1.0)
    ref_source = problem._spec.ref_source
    return ref_source.replace("class Model(", "class ModelNew(")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanity-check a YAML problem against the eval worker")
    parser.add_argument("yaml_path", help="Path to the YAML problem file")
    parser.add_argument("--kernel", help="Path to a custom kernel file (must define ModelNew)")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Eval worker URL (default: http://127.0.0.1:8080)")
    parser.add_argument("--stage", choices=["quick", "full"], default="quick", help="Benchmark stage (default: quick)")
    parser.add_argument("--timeout", type=float, default=120.0, help="Request timeout in seconds (default: 120)")
    parser.add_argument("--dump-response", action="store_true", help="Dump full JSON response")
    args = parser.parse_args()

    yaml_path = Path(args.yaml_path).resolve()
    if not yaml_path.exists():
        print(f"ERROR: {yaml_path} does not exist")
        sys.exit(1)

    # Load problem
    print(f"Loading problem: {yaml_path.name}")
    problem = YamlProblem.from_yaml_path(yaml_path)
    problem_id = problem.problem_id()
    print(f"  problem_id: {problem_id}")
    print(f"  backend:    {problem.config.backend}")
    print(f"  precision:  {problem.config.precision}")

    # Build candidate
    source = load_candidate_source(problem, args.kernel)
    if args.kernel:
        print(f"  kernel:     {args.kernel}")
    else:
        print(f"  kernel:     <reference as ModelNew>")

    # Verify ModelNew exists in source
    if "class ModelNew" not in source:
        print("ERROR: candidate source must define 'class ModelNew'")
        sys.exit(1)

    run_id = str(uuid4())
    candidate = problem._make_candidate(
        run_id=run_id,
        source=source,
        operation="test",
        agent_id="test_problem_script",
        hypothesis="sanity check",
    )
    attach_content_hashes(candidate=candidate)

    stage = BenchmarkStage(args.stage)
    config_dict = problem.to_config_dict()

    # Health check
    print(f"\nConnecting to eval worker at {args.url} ...")
    client = RemoteEvaluatorClient(args.url, timeout_s=args.timeout)
    try:
        import urllib.request
        req = urllib.request.Request(f"{args.url}/healthz")
        with urllib.request.urlopen(req, timeout=10) as resp:
            health = json.loads(resp.read().decode())
        print(f"  status: {health.get('status', '?')}")
        print(f"  problems: {health.get('problems', [])}")
    except Exception as e:
        print(f"  WARNING: health check failed: {e}")
        print(f"  (continuing anyway — the evaluate endpoint may still work)")

    # Submit evaluation
    print(f"\nSubmitting evaluation (stage={args.stage}) ...")
    t0 = time.time()
    try:
        result = client.evaluate(
            problem_id=problem_id,
            candidate=candidate,
            stage=stage,
            problem_config=config_dict,
        )
    except RemoteEvaluationError as e:
        print(f"\nERROR: evaluation failed: {e}")
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"  completed in {elapsed:.1f}s")

    # Display results
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")

    print(f"  scalar_fitness: {result.scalar_fitness}")

    if result.static_check:
        sc = result.static_check
        print(f"\n  Static check: {'PASS' if sc.ok else 'FAIL'}")
        if sc.reasons:
            for r in sc.reasons:
                print(f"    - {r}")

    if result.build_result:
        br = result.build_result
        print(f"\n  Build: {br.status.value} ({br.duration_ms}ms)")

    if result.validation_result:
        vr = result.validation_result
        print(f"\n  Validation: {vr.status.value} ({vr.tests_passed}/{vr.tests_total} passed)")
        print(f"    max_abs_error: {vr.max_abs_error:.2e}")
        print(f"    max_rel_error: {vr.max_rel_error:.2e}")
        if vr.failing_cases:
            for fc in vr.failing_cases[:5]:
                print(f"    FAIL: {fc.summary}")

    if result.benchmark_result:
        bm = result.benchmark_result
        t = bm.timing
        print(f"\n  Benchmark: {bm.status.value} ({bm.samples} samples)")
        print(f"    median:  {t.median_us:.1f} us")
        print(f"    mean:    {t.mean_us:.1f} us")
        print(f"    p95:     {t.p95_us:.1f} us")
        print(f"    stdev:   {t.stdev_us:.1f} us (CoV={t.cov:.3f})")

        ref_ms = bm.profile.get("ref_runtime_ms")
        speedup = bm.profile.get("speedup_vs_ref")
        if ref_ms is not None:
            print(f"\n  Reference latency: {ref_ms:.3f} ms")
            print(f"  Speedup vs ref:    {speedup:.3f}x")
        else:
            print(f"\n  WARNING: no reference latency in profile!")
            print(f"  profile: {bm.profile}")

    # Dump full response if requested
    if args.dump_response:
        print(f"\n{'='*60}")
        print("FULL RESPONSE")
        print(f"{'='*60}")
        dump = {
            "scalar_fitness": result.scalar_fitness,
            "raw_score": result.raw_score,
        }
        if result.static_check:
            dump["static_check"] = to_dict(result.static_check)
        if result.build_result:
            dump["build_result"] = to_dict(result.build_result)
        if result.validation_result:
            dump["validation_result"] = to_dict(result.validation_result)
        if result.benchmark_result:
            dump["benchmark_result"] = to_dict(result.benchmark_result)
        print(json.dumps(dump, indent=2, default=str))

    # Exit code based on success
    if result.scalar_fitness is not None and result.scalar_fitness > 0:
        print("\nSANITY CHECK PASSED")
        sys.exit(0)
    else:
        print("\nSANITY CHECK FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
