from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import PipelineConfig, SingleWorkerPipeline
from .plugins.vector_add import VectorAddConfig, VectorAddProblem


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kernelswarm")
    sub = parser.add_subparsers(dest="command", required=True)

    run_vec = sub.add_parser("run-vector-add", help="Run the vector_add example problem")
    run_vec.add_argument("--workspace", type=Path, default=Path(".runs/dev"))
    run_vec.add_argument("--seed", type=int, default=42)
    run_vec.add_argument("--top-k-full", type=int, default=2)
    run_vec.add_argument("--seed-count", type=int, default=4)
    run_vec.add_argument("--quick-size", type=int, default=20_000)
    run_vec.add_argument("--full-size", type=int, default=100_000)
    run_vec.add_argument("--quick-iters", type=int, default=15)
    run_vec.add_argument("--full-iters", type=int, default=40)
    run_vec.add_argument("--quick-warmup", type=int, default=3)
    run_vec.add_argument("--full-warmup", type=int, default=6)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-vector-add":
        problem = VectorAddProblem(
            VectorAddConfig(
                quick_size=args.quick_size,
                full_size=args.full_size,
                quick_iters=args.quick_iters,
                full_iters=args.full_iters,
                quick_warmup=args.quick_warmup,
                full_warmup=args.full_warmup,
                seed_count=args.seed_count,
            )
        )
        pipeline = SingleWorkerPipeline(
            PipelineConfig(
                workspace=args.workspace,
                seed=args.seed,
                full_benchmark_top_k=args.top_k_full,
            )
        )
        summary = pipeline.run(problem)
        print(f"run_id={summary.run_id}")
        print(f"problem={summary.problem_id}")
        print(f"candidates={summary.total_candidates}")
        print(f"best_candidate_id={summary.best_candidate_id}")
        print(f"best_fitness={summary.best_fitness}")
        print(f"report={summary.report_path}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
