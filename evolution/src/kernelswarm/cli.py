from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .brev_api import BrevClient
from .dashboard import DashboardServer, DashboardService
from .nemotron import DEFAULT_NEMOTRON_MODEL, DEFAULT_PROVIDER
from .pipeline import PipelineConfig, SingleWorkerPipeline
from .plugins.kernelbench import KernelBenchConfig, KernelBenchProblem
from .plugins.vector_add import VectorAddConfig, VectorAddProblem
from .registry import default_problem_factories
from .remote import EvalWorkerServer, EvalWorkerService
from .search import BrevSearchConfig, SearchConfig, SwarmSearchRunner


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


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
    run_vec.add_argument("--backend", type=str, choices=["python-sim", "nvcc"], default="python-sim")
    run_vec.add_argument("--block-size", type=int, default=256)
    run_vec.add_argument("--remote-eval-url", type=str, default=None)
    run_vec.add_argument("--remote-timeout-s", type=float, default=120.0)

    run_kb = sub.add_parser("run-kernelbench", help="Run a KernelBench problem through the pipeline")
    run_kb.add_argument("--workspace", type=Path, default=Path(".runs/kernelbench"))
    run_kb.add_argument("--seed", type=int, default=42)
    run_kb.add_argument("--top-k-full", type=int, default=1)
    run_kb.add_argument("--level", type=int, default=1)
    run_kb.add_argument("--problem-id", type=int, default=23)
    run_kb.add_argument("--dataset-source", type=str, choices=["local", "huggingface"], default="local")
    run_kb.add_argument("--dataset-name", type=str, default="ScalingIntelligence/KernelBench")
    run_kb.add_argument("--dataset-base-path", type=str, default=None)
    run_kb.add_argument("--repo-path", type=str, default=None)
    run_kb.add_argument("--backend", type=str, default="cuda")
    run_kb.add_argument("--precision", type=str, choices=["fp16", "fp32", "bf16"], default="fp32")
    run_kb.add_argument("--device", type=int, default=0)
    run_kb.add_argument("--timing-method", type=str, default="cuda_event")
    run_kb.add_argument("--seed-count", type=int, default=3)
    run_kb.add_argument("--quick-correct-trials", type=int, default=1)
    run_kb.add_argument("--quick-perf-trials", type=int, default=4)
    run_kb.add_argument("--full-correct-trials", type=int, default=1)
    run_kb.add_argument("--full-perf-trials", type=int, default=20)
    run_kb.add_argument("--build-dir-root", type=str, default=None)
    run_kb.add_argument("--static-check-enabled", action=argparse.BooleanOptionalAction, default=True)
    run_kb.add_argument("--static-fail-on-warning", action=argparse.BooleanOptionalAction, default=False)
    run_kb.add_argument("--verbose", action=argparse.BooleanOptionalAction, default=False)
    run_kb.add_argument("--remote-eval-url", type=str, default=None)
    run_kb.add_argument("--remote-timeout-s", type=float, default=300.0)

    run_search = sub.add_parser("run-swarm-search", help="Run MAP-Elites swarm search")
    run_search.add_argument("--workspace", type=Path, default=Path(".runs/search"))
    run_search.add_argument("--problem-id", type=str, default="vector_add_v1")
    run_search.add_argument("--seed", type=int, default=42)
    run_search.add_argument("--max-iterations", type=int, default=200)
    run_search.add_argument("--max-minutes", type=float, default=30.0)
    run_search.add_argument("--token-budget", type=int, default=2_000_000)
    run_search.add_argument("--migration-every-updates", type=int, default=50)
    run_search.add_argument("--migration-packet-size", type=int, default=3)
    run_search.add_argument("--checkpoint-every-iterations", type=int, default=100)
    run_search.add_argument("--checkpoint-every-seconds", type=float, default=300.0)
    run_search.add_argument("--checkpoint-path", type=Path, default=None)
    run_search.add_argument("--resume", action="store_true")
    run_search.add_argument("--resume-run-id", type=str, default=None)
    run_search.add_argument("--generators", type=int, default=32)
    run_search.add_argument("--judges", type=int, default=32)
    run_search.add_argument("--llm-enabled", action=argparse.BooleanOptionalAction, default=True)
    run_search.add_argument("--llm-disabled", action="store_false", dest="llm_enabled")
    run_search.add_argument(
        "--nemotron-provider",
        type=str,
        choices=["deepinfra", "nvidia", "custom"],
        default=DEFAULT_PROVIDER,
    )
    run_search.add_argument("--nemotron-model", type=str, default=DEFAULT_NEMOTRON_MODEL)
    run_search.add_argument("--nemotron-base-url", type=str, default=None)
    run_search.add_argument("--nemotron-api-key", type=str, default=None)
    run_search.add_argument("--nemotron-api-key-env", type=str, default=None)
    run_search.add_argument("--nemotron-max-concurrent-requests", type=int, default=32)
    run_search.add_argument(
        "--remote-eval-url",
        type=str,
        default=None,
        help="Remote eval base URL. Accepts a single URL or a comma-separated list for round-robin fanout.",
    )
    run_search.add_argument("--remote-timeout-s", type=float, default=120.0)
    run_search.add_argument("--proposal-workers", type=int, default=32)
    run_search.add_argument("--quick-eval-workers", type=int, default=12)
    run_search.add_argument("--full-eval-workers", type=int, default=4)
    run_search.add_argument("--max-inflight-proposals", type=int, default=96)
    run_search.add_argument("--max-inflight-quick-evals", type=int, default=32)
    run_search.add_argument("--max-inflight-full-evals", type=int, default=8)
    run_search.add_argument("--periodic-full-eval-every-quick", type=int, default=40)
    run_search.add_argument("--force-first-full-per-island", action=argparse.BooleanOptionalAction, default=True)
    run_search.add_argument("--brev-instance", type=str, default=None)
    run_search.add_argument("--brev-machine", type=str, default="n1-highmem-4:nvidia-tesla-t4:1")
    run_search.add_argument("--brev-create-if-missing", action="store_true")
    run_search.add_argument(
        "--backend",
        type=str,
        choices=["python-sim", "nvcc", "cuda", "triton", "tilelang", "cute", "thunderkittens", "cutlass"],
        default="python-sim",
    )
    run_search.add_argument("--block-size", type=int, default=256)
    run_search.add_argument("--seed-count", type=int, default=4)
    run_search.add_argument("--quick-size", type=int, default=20_000)
    run_search.add_argument("--full-size", type=int, default=100_000)
    run_search.add_argument("--quick-iters", type=int, default=15)
    run_search.add_argument("--full-iters", type=int, default=40)
    run_search.add_argument("--quick-warmup", type=int, default=3)
    run_search.add_argument("--full-warmup", type=int, default=6)
    run_search.add_argument("--kb-level", type=int, default=1)
    run_search.add_argument("--kb-problem-id", type=int, default=23)
    run_search.add_argument("--kb-dataset-source", type=str, choices=["local", "huggingface"], default="local")
    run_search.add_argument("--kb-dataset-name", type=str, default="ScalingIntelligence/KernelBench")
    run_search.add_argument("--kb-dataset-base-path", type=str, default=None)
    run_search.add_argument("--kb-repo-path", type=str, default=None)
    run_search.add_argument("--kb-precision", type=str, choices=["fp16", "fp32", "bf16"], default="fp32")
    run_search.add_argument("--kb-device", type=int, default=0)
    run_search.add_argument("--kb-timing-method", type=str, default="cuda_event")
    run_search.add_argument("--kb-quick-correct-trials", type=int, default=1)
    run_search.add_argument("--kb-quick-perf-trials", type=int, default=4)
    run_search.add_argument("--kb-full-correct-trials", type=int, default=1)
    run_search.add_argument("--kb-full-perf-trials", type=int, default=20)
    run_search.add_argument("--kb-build-dir-root", type=str, default=None)
    run_search.add_argument("--kb-static-check-enabled", action=argparse.BooleanOptionalAction, default=True)
    run_search.add_argument("--kb-static-fail-on-warning", action=argparse.BooleanOptionalAction, default=False)
    run_search.add_argument("--kb-verbose", action=argparse.BooleanOptionalAction, default=False)

    brev_ensure = sub.add_parser("brev-ensure-instance", help="Ensure Brev instance is running and shell-ready")
    brev_ensure.add_argument("--name", type=str, required=True)
    brev_ensure.add_argument("--machine", type=str, default="n1-highmem-4:nvidia-tesla-t4:1")
    brev_ensure.add_argument("--create-if-missing", action="store_true")
    brev_ensure.add_argument("--wait-timeout-s", type=float, default=600.0)

    serve = sub.add_parser("serve-eval-worker", help="Run a minimal HTTP eval worker")
    serve.add_argument("--host", type=str, default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    dashboard = sub.add_parser("serve-dashboard", help="Run local dashboard API + UI server")
    dashboard.add_argument("--workspace", type=Path, default=Path(".runs/search"))
    dashboard.add_argument("--host", type=str, default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8090)

    return parser


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(Path.cwd() / ".env")
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
                backend=args.backend,
                default_block_size=args.block_size,
            )
        )
        pipeline = SingleWorkerPipeline(
            PipelineConfig(
                workspace=args.workspace,
                seed=args.seed,
                full_benchmark_top_k=args.top_k_full,
                remote_eval_url=args.remote_eval_url,
                remote_eval_timeout_s=args.remote_timeout_s,
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

    if args.command == "run-kernelbench":
        problem = KernelBenchProblem(
            KernelBenchConfig(
                level=args.level,
                problem_id=args.problem_id,
                dataset_source=args.dataset_source,
                dataset_name=args.dataset_name,
                dataset_base_path=args.dataset_base_path,
                repo_path=args.repo_path,
                backend=args.backend,
                precision=args.precision,
                device=args.device,
                timing_method=args.timing_method,
                seed_count=args.seed_count,
                quick_correct_trials=args.quick_correct_trials,
                quick_perf_trials=args.quick_perf_trials,
                full_correct_trials=args.full_correct_trials,
                full_perf_trials=args.full_perf_trials,
                build_dir_root=args.build_dir_root,
                static_check_enabled=args.static_check_enabled,
                static_fail_on_warning=args.static_fail_on_warning,
                verbose=args.verbose,
            )
        )
        pipeline = SingleWorkerPipeline(
            PipelineConfig(
                workspace=args.workspace,
                seed=args.seed,
                full_benchmark_top_k=args.top_k_full,
                remote_eval_url=args.remote_eval_url,
                remote_eval_timeout_s=args.remote_timeout_s,
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

    if args.command == "run-swarm-search":
        factories = default_problem_factories()
        if args.problem_id not in factories:
            parser.error(f"unknown problem-id: {args.problem_id}; available={','.join(sorted(factories.keys()))}")

        backend = args.backend
        if args.problem_id == "kernelbench_v1" and backend == "python-sim":
            backend = "cuda"

        problem_config = {
            "backend": backend,
            "default_block_size": args.block_size,
            "seed_count": args.seed_count,
            "quick_size": args.quick_size,
            "full_size": args.full_size,
            "quick_iters": args.quick_iters,
            "full_iters": args.full_iters,
            "quick_warmup": args.quick_warmup,
            "full_warmup": args.full_warmup,
            "level": args.kb_level,
            "problem_id": args.kb_problem_id,
            "dataset_source": args.kb_dataset_source,
            "dataset_name": args.kb_dataset_name,
            "dataset_base_path": args.kb_dataset_base_path,
            "repo_path": args.kb_repo_path,
            "precision": args.kb_precision,
            "device": args.kb_device,
            "timing_method": args.kb_timing_method,
            "quick_correct_trials": args.kb_quick_correct_trials,
            "quick_perf_trials": args.kb_quick_perf_trials,
            "full_correct_trials": args.kb_full_correct_trials,
            "full_perf_trials": args.kb_full_perf_trials,
            "build_dir_root": args.kb_build_dir_root,
            "static_check_enabled": args.kb_static_check_enabled,
            "static_fail_on_warning": args.kb_static_fail_on_warning,
            "verbose": args.kb_verbose,
        }
        problem = factories[args.problem_id](problem_config)
        runner = SwarmSearchRunner(
            SearchConfig(
                workspace=args.workspace,
                seed=args.seed,
                max_iterations=args.max_iterations,
                max_minutes=args.max_minutes,
                token_budget=args.token_budget,
                migration_every_updates=args.migration_every_updates,
                migration_packet_size=args.migration_packet_size,
                checkpoint_every_iterations=args.checkpoint_every_iterations,
                checkpoint_every_seconds=args.checkpoint_every_seconds,
                checkpoint_path=args.checkpoint_path,
                resume=args.resume,
                resume_run_id=args.resume_run_id,
                generator_agents=args.generators,
                judge_agents=args.judges,
                llm_enabled=args.llm_enabled,
                nemotron_provider=args.nemotron_provider,
                nemotron_model=args.nemotron_model,
                nemotron_base_url=args.nemotron_base_url,
                nemotron_api_key=args.nemotron_api_key,
                nemotron_api_key_env=args.nemotron_api_key_env,
                nemotron_max_concurrent_requests=args.nemotron_max_concurrent_requests,
                remote_eval_url=args.remote_eval_url,
                remote_eval_timeout_s=args.remote_timeout_s,
                proposal_workers=args.proposal_workers,
                quick_eval_workers=args.quick_eval_workers,
                full_eval_workers=args.full_eval_workers,
                max_inflight_proposals=args.max_inflight_proposals,
                max_inflight_quick_evals=args.max_inflight_quick_evals,
                max_inflight_full_evals=args.max_inflight_full_evals,
                periodic_full_eval_every_quick=args.periodic_full_eval_every_quick,
                force_first_full_per_island=args.force_first_full_per_island,
                brev=BrevSearchConfig(
                    instance_name=args.brev_instance,
                    machine=args.brev_machine,
                    create_if_missing=args.brev_create_if_missing,
                ),
            )
        )
        summary = runner.run(problem)
        print(f"run_id={summary.run_id}")
        print(f"problem={summary.problem_id}")
        print(f"candidates={summary.total_candidates}")
        print(f"quick_scored={summary.quick_scored}")
        print(f"full_scored={summary.full_scored}")
        print(f"best_candidate_id={summary.best_candidate_id}")
        print(f"best_fitness={summary.best_fitness}")
        print(f"report={summary.report_path}")
        return 0

    if args.command == "brev-ensure-instance":
        client = BrevClient()
        instance = client.ensure_instance(
            name=args.name,
            machine=args.machine,
            create_if_missing=args.create_if_missing,
            wait_timeout_s=args.wait_timeout_s,
        )
        print(f"name={instance.name}")
        print(f"status={instance.status}")
        print(f"shell={instance.shell}")
        print(f"instance_id={instance.instance_id}")
        print(f"machine={instance.machine}")
        return 0

    if args.command == "serve-eval-worker":
        service = EvalWorkerService(default_problem_factories())
        server = EvalWorkerServer(args.host, args.port, service)
        print(f"eval_worker_url={server.base_url}")
        print("registered_problems=" + ",".join(service.list_problem_ids()))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
        return 0

    if args.command == "serve-dashboard":
        service = DashboardService(args.workspace)
        server = DashboardServer(args.host, args.port, service)
        print(f"dashboard_url={server.base_url}")
        print(f"workspace={args.workspace}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
