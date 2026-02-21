from __future__ import annotations

import base64
import json
import pickle
import random
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agents import GeneratorDecision, JudgeDecision, SwarmAgentPool
from .artifacts import ArtifactStore
from .brev_api import BrevClient, BrevError
from .hashing import attach_content_hashes
from .manifest import build_run_manifest
from .map_elites import (
    DescriptorAxes,
    IslandState,
    MapElitesArchive,
    default_island_policies,
    finite_fitness,
    migrate_ring,
    scalarize_raw_score,
)
from .models import (
    BenchmarkResult,
    BenchmarkStage,
    BenchmarkStatus,
    BenchmarkTiming,
    BuildExecution,
    BuildResult,
    BuildStatus,
    Candidate,
    CandidateState,
    Descriptor,
    IterationMetric,
    RunSummary,
    ScoreRecord,
    StaticCheckResult,
    ValidationResult,
    ValidationStatus,
)
from .nemotron import DEFAULT_NEMOTRON_MODEL, DEFAULT_PROVIDER, NemotronClient, NemotronConfig
from .persistence import SQLiteStore
from .remote import (
    RemoteEvaluationError,
    RemoteEvaluationResult,
    RemoteEvaluatorClient,
    candidate_from_dict,
)
from .sdk import OptimizationProblem, ProblemRunContext
from .serialization import to_dict


@dataclass(slots=True)
class BrevSearchConfig:
    instance_name: str | None = None
    machine: str = "n1-highmem-4:nvidia-tesla-t4:1"
    create_if_missing: bool = False
    wait_timeout_s: float = 600.0


@dataclass(slots=True)
class SearchConfig:
    workspace: Path
    seed: int = 42
    max_iterations: int = 200
    max_minutes: float = 30.0
    token_budget: int = 2_000_000
    full_trigger_ratio: float = 0.98
    migration_every_updates: int = 50
    migration_packet_size: int = 3
    checkpoint_every_iterations: int = 100
    checkpoint_every_seconds: float = 300.0
    checkpoint_path: Path | None = None
    resume: bool = False
    resume_run_id: str | None = None
    descriptor_axes: DescriptorAxes = field(default_factory=DescriptorAxes)
    generator_agents: int = 32
    judge_agents: int = 32
    llm_enabled: bool = True
    nemotron_provider: str = DEFAULT_PROVIDER
    nemotron_model: str = DEFAULT_NEMOTRON_MODEL
    nemotron_base_url: str | None = None
    nemotron_api_key: str | None = None
    nemotron_api_key_env: str | None = None
    nemotron_max_concurrent_requests: int = 32
    remote_eval_url: str | None = None
    remote_eval_timeout_s: float = 120.0
    proposal_workers: int = 32
    quick_eval_workers: int = 12
    full_eval_workers: int = 4
    max_inflight_proposals: int = 96
    max_inflight_quick_evals: int = 32
    max_inflight_full_evals: int = 8
    periodic_full_eval_every_quick: int = 40
    force_first_full_per_island: bool = True
    brev: BrevSearchConfig = field(default_factory=BrevSearchConfig)


@dataclass(slots=True)
class CandidateEvaluation:
    candidate: Candidate
    static_check: StaticCheckResult
    judge: JudgeDecision | None
    build_result: BuildResult | None
    validation_result: ValidationResult | None
    quick_benchmark: BenchmarkResult | None
    quick_score: ScoreRecord
    descriptor: Descriptor | None
    build_execution: BuildExecution | None = None
    full_score: ScoreRecord | None = None
    full_benchmark: BenchmarkResult | None = None
    full_judge: JudgeDecision | None = None


@dataclass(slots=True)
class SearchState:
    run_id: str
    iteration: int
    accepted_updates: int
    quick_scored: int
    full_scored: int


class SwarmSearchRunner:
    def __init__(self, config: SearchConfig) -> None:
        self.config = config
        self.workspace = config.workspace
        self.db_path = self.workspace / "db" / "runs.sqlite"
        self.artifacts_root = self.workspace / "artifacts"
        self._rng = random.Random(config.seed)

    def run(self, problem: OptimizationProblem) -> RunSummary:
        self.workspace.mkdir(parents=True, exist_ok=True)
        store = SQLiteStore(self.db_path)
        artifacts = ArtifactStore(self.artifacts_root)
        remote_clients = [
            RemoteEvaluatorClient(url, timeout_s=self.config.remote_eval_timeout_s)
            for url in self._remote_eval_urls()
        ]
        remote_client = remote_clients[0] if remote_clients else None
        problem_config = self._problem_config(problem)

        brev_instance: dict[str, Any] | None = None
        if self.config.brev.instance_name:
            brev_instance = self._ensure_brev_instance()

        llm_client = self._build_llm_client()
        swarm = SwarmAgentPool.create(
            client=llm_client,
            rng=self._rng,
            generator_count=self.config.generator_agents,
            judge_count=self.config.judge_agents,
        )
        prompt_context = self._get_prompt_context(problem)
        islands = self._init_islands()
        island_quick_frontier: dict[str, float | None] = {island.policy.island_id: None for island in islands}
        candidates_by_id: dict[str, Candidate] = {}
        seen_hashes: set[str] = set()
        parent_failure_hints: dict[str, list[str]] = {}  # parent_id → [error summaries]

        run_id = str(uuid4())
        state = SearchState(run_id=run_id, iteration=0, accepted_updates=0, quick_scored=0, full_scored=0)
        checkpoint_path = self._resolve_checkpoint_path()

        if self.config.resume:
            if self.config.resume_run_id:
                state.run_id = self.config.resume_run_id
            loaded = self._load_checkpoint(checkpoint_path)
            if loaded is not None:
                state = SearchState(
                    run_id=loaded["run_id"],
                    iteration=int(loaded.get("iteration", 0)),
                    accepted_updates=int(loaded.get("accepted_updates", 0)),
                    quick_scored=int(loaded.get("quick_scored", 0)),
                    full_scored=int(loaded.get("full_scored", 0)),
                )
                islands = self._restore_islands(loaded.get("islands", []))
                candidates_by_id = {
                    key: candidate_from_dict(value)
                    for key, value in dict(loaded.get("candidates", {})).items()
                }
                for candidate in candidates_by_id.values():
                    if not candidate.content_hash:
                        attach_content_hashes(candidate=candidate)
                    seen_hashes.add(candidate.content_hash)
                self._restore_rng_state(dict(loaded))

        run_dir = artifacts.run_dir(state.run_id)
        checkpoint_path = self._resolve_checkpoint_path()

        try:
            if not store.run_exists(state.run_id):
                manifest = build_run_manifest(
                    run_id=state.run_id,
                    problem_id=problem.problem_id(),
                    seed=self.config.seed,
                    repo_root=Path.cwd(),
                )
                store.start_run(
                    run_id=state.run_id,
                    problem_id=problem.problem_id(),
                    manifest=manifest,
                    config=to_dict(self.config),
                )
                artifacts.write_json(run_dir / "manifest.json", manifest)
            else:
                manifest = {}

            if not candidates_by_id:
                self._seed_initial_population(
                    problem=problem,
                    state=state,
                    store=store,
                    artifacts=artifacts,
                    islands=islands,
                    swarm=swarm,
                    candidates_by_id=candidates_by_id,
                    seen_hashes=seen_hashes,
                    remote_client=remote_client,
                    problem_config=problem_config,
                    island_quick_frontier=island_quick_frontier,
                    prompt_context=prompt_context,
                )

            start_time = time.time()
            last_checkpoint = time.time()
            island_by_id = {island.policy.island_id: island for island in islands}
            island_quick_counts = {island.policy.island_id: 0 for island in islands}
            island_full_attempts = {island.policy.island_id: 0 for island in islands}

            proposal_workers = max(1, min(self.config.proposal_workers, self.config.generator_agents))
            quick_eval_workers = max(1, self.config.quick_eval_workers)
            full_eval_workers = max(1, self.config.full_eval_workers)
            max_inflight_proposals = max(proposal_workers, self.config.max_inflight_proposals)
            max_inflight_quick = max(quick_eval_workers, self.config.max_inflight_quick_evals)
            max_inflight_full = max(full_eval_workers, self.config.max_inflight_full_evals)

            proposal_futures: dict[Future[GeneratorDecision], tuple[int, str]] = {}
            quick_eval_futures: dict[Future[CandidateEvaluation], tuple[int, str, Candidate]] = {}
            full_eval_futures: dict[
                Future[tuple[ScoreRecord, BenchmarkResult] | None],
                tuple[int, str, Candidate, CandidateEvaluation, RemoteEvaluatorClient | None],
            ] = {}
            pending_quick: deque[tuple[int, str, Candidate, GeneratorDecision, RemoteEvaluatorClient | None]] = deque()
            pending_full: deque[tuple[int, str, Candidate, CandidateEvaluation, RemoteEvaluatorClient | None]] = deque()
            next_iteration = int(state.iteration)

            with (
                ThreadPoolExecutor(max_workers=proposal_workers, thread_name_prefix="ks-propose") as proposal_exec,
                ThreadPoolExecutor(max_workers=quick_eval_workers, thread_name_prefix="ks-eval-quick") as quick_exec,
                ThreadPoolExecutor(max_workers=full_eval_workers, thread_name_prefix="ks-eval-full") as full_exec,
            ):
                while True:
                    budget_exhausted = self._submission_budget_exhausted(
                        next_iteration=next_iteration,
                        start_time=start_time,
                        swarm=swarm,
                    )

                    while (
                        not budget_exhausted
                        and len(proposal_futures) + len(pending_quick) < max_inflight_proposals
                    ):
                        island = islands[next_iteration % len(islands)]
                        parent = self._select_parent_candidate(island, candidates_by_id)
                        if parent is None:
                            budget_exhausted = True
                            break

                        generator = swarm.next_generator()
                        iteration = next_iteration
                        next_iteration += 1
                        ctx = prompt_context
                        hints = parent_failure_hints.get(parent.candidate_id)
                        if hints:
                            ctx = dict(prompt_context) if prompt_context else {}
                            ctx["recent_failures"] = hints[:3]
                        future = proposal_exec.submit(
                            generator.propose,
                            parent=parent,
                            policy=island.policy,
                            prompt_context=ctx,
                        )
                        proposal_futures[future] = (iteration, island.policy.island_id)
                        budget_exhausted = self._submission_budget_exhausted(
                            next_iteration=next_iteration,
                            start_time=start_time,
                            swarm=swarm,
                        )

                    while pending_quick and len(quick_eval_futures) < max_inflight_quick:
                        iteration, island_id, candidate, proposal, chosen_remote = pending_quick.popleft()
                        eval_future = quick_exec.submit(
                            self._evaluate_candidate,
                            problem=problem,
                            store=store,
                            artifacts=artifacts,
                            candidate=candidate,
                            swarm=swarm,
                            remote_client=chosen_remote,
                            problem_config=problem_config,
                            judge_input=proposal,
                            prompt_context=prompt_context,
                        )
                        quick_eval_futures[eval_future] = (iteration, island_id, candidate)

                    while pending_full and len(full_eval_futures) < max_inflight_full:
                        iteration, island_id, candidate, eval_result, chosen_remote = pending_full.popleft()
                        full_future = full_exec.submit(
                            self._evaluate_full,
                            problem=problem,
                            store=store,
                            artifacts=artifacts,
                            candidate=candidate,
                            prior_eval=eval_result,
                            remote_client=chosen_remote,
                            problem_config=problem_config,
                        )
                        full_eval_futures[full_future] = (
                            iteration,
                            island_id,
                            candidate,
                            eval_result,
                            chosen_remote,
                        )

                    completed_any = False

                    for future in [f for f in proposal_futures if f.done()]:
                        completed_any = True
                        iteration, island_id = proposal_futures.pop(future)
                        state.iteration = max(state.iteration, iteration + 1)
                        island = island_by_id[island_id]
                        try:
                            proposal = future.result()
                        except Exception as exc:
                            self._record_iteration_metrics(
                                store=store,
                                state=state,
                                iteration=iteration,
                                islands=islands,
                                swarm=swarm,
                                active_island_id=island_id,
                                candidate_id=None,
                                quick_fitness=None,
                                full_fitness=None,
                                quick_median_us=None,
                                full_median_us=None,
                                reason=f"generator_error:{type(exc).__name__}",
                            )
                            continue

                        swarm.usage.add(proposal.usage)
                        if proposal.rejected:
                            self._record_iteration_metrics(
                                store=store,
                                state=state,
                                iteration=iteration,
                                islands=islands,
                                swarm=swarm,
                                active_island_id=island_id,
                                candidate_id=None,
                                quick_fitness=None,
                                full_fitness=None,
                                quick_median_us=None,
                                full_median_us=None,
                                reason="generator_rejected",
                            )
                            continue

                        candidate = proposal.candidate
                        if candidate.content_hash in seen_hashes:
                            self._record_iteration_metrics(
                                store=store,
                                state=state,
                                iteration=iteration,
                                islands=islands,
                                swarm=swarm,
                                active_island_id=island.policy.island_id,
                                candidate_id=candidate.candidate_id,
                                quick_fitness=None,
                                full_fitness=None,
                                quick_median_us=None,
                                full_median_us=None,
                                reason="duplicate_candidate",
                            )
                            continue
                        seen_hashes.add(candidate.content_hash)
                        candidates_by_id[candidate.candidate_id] = candidate
                        pending_quick.append(
                            (
                                iteration,
                                island.policy.island_id,
                                candidate,
                                proposal,
                                self._select_remote_client(remote_clients, candidate),
                            )
                        )

                    for future in [f for f in quick_eval_futures if f.done()]:
                        completed_any = True
                        iteration, island_id, candidate = quick_eval_futures.pop(future)
                        state.iteration = max(state.iteration, iteration + 1)
                        island = island_by_id[island_id]
                        try:
                            eval_result = future.result()
                        except Exception as exc:
                            eval_result = self._record_internal_eval_error(
                                store=store,
                                artifacts=artifacts,
                                candidate=candidate,
                                reason=f"quick_eval_internal_error:{type(exc).__name__}",
                            )

                        state.quick_scored += 1
                        island_quick_counts[island_id] = island_quick_counts.get(island_id, 0) + 1
                        quick_fitness = float(eval_result.quick_score.scalar_fitness)
                        quick_baseline = island_quick_frontier.get(island_id)
                        should_threshold = self._should_run_full(
                            quick_fitness=quick_fitness,
                            quick_baseline=quick_baseline,
                        )
                        self._update_quick_frontier(
                            frontier=island_quick_frontier,
                            island_id=island_id,
                            quick_fitness=quick_fitness,
                        )

                        self._record_iteration_metrics(
                            store=store,
                            state=state,
                            iteration=iteration,
                            islands=islands,
                            swarm=swarm,
                            active_island_id=island.policy.island_id,
                            candidate_id=candidate.candidate_id,
                            quick_fitness=quick_fitness,
                            full_fitness=None,
                            quick_median_us=self._benchmark_median_us(eval_result.quick_benchmark),
                            full_median_us=None,
                            reason="quick_evaluated",
                        )

                        # Track failure hints for the parent so future mutations avoid the same mistakes.
                        if quick_fitness <= -1e17:
                            hint = self._extract_failure_hint(eval_result)
                            if hint and candidate.parent_ids:
                                pid = candidate.parent_ids[0]
                                bucket = parent_failure_hints.setdefault(pid, [])
                                if len(bucket) < 3:
                                    bucket.append(hint)

                        full_attempted = island_full_attempts.get(island_id, 0)
                        should_force_first = self.config.force_first_full_per_island and full_attempted == 0
                        periodic_every = max(0, int(self.config.periodic_full_eval_every_quick))
                        should_periodic = periodic_every > 0 and (island_quick_counts[island_id] % periodic_every == 0)
                        if should_force_first or should_periodic or should_threshold:
                            top = island.archive.top_elites(1)
                            island_top_fitness = float(top[0].fitness) if top else None
                            full_gate = swarm.next_judge().review(
                                candidate=candidate,
                                static=eval_result.static_check,
                                stage="full_gate",
                                quick_fitness=quick_fitness,
                                quick_median_us=self._benchmark_median_us(eval_result.quick_benchmark),
                                island_top_fitness=island_top_fitness,
                                prompt_context=prompt_context,
                            )
                            swarm.usage.add(full_gate.usage)
                            eval_result.full_judge = full_gate
                            artifacts.write_json(
                                artifacts.candidate_dir(candidate.run_id, candidate.candidate_id, "agent")
                                / "judge_full_decision.json",
                                full_gate,
                            )

                            if full_gate.compile_worthy:
                                island_full_attempts[island_id] = full_attempted + 1
                                pending_full.append(
                                    (
                                        iteration,
                                        island.policy.island_id,
                                        candidate,
                                        eval_result,
                                        self._select_remote_client(remote_clients, candidate),
                                    )
                                )

                    for future in [f for f in full_eval_futures if f.done()]:
                        completed_any = True
                        iteration, island_id, candidate, eval_result, _chosen_remote = full_eval_futures.pop(future)
                        state.iteration = max(state.iteration, iteration + 1)
                        island = island_by_id[island_id]

                        full = None
                        try:
                            full = future.result()
                        except Exception:
                            full = None
                        if full is not None:
                            eval_result.full_score = full[0]
                            eval_result.full_benchmark = full[1]
                            state.full_scored += 1
                            self._apply_archive_update(
                                state=state,
                                island=island,
                                islands=islands,
                                candidates_by_id=candidates_by_id,
                                artifacts=artifacts,
                                run_dir=run_dir,
                                candidate_id=candidate.candidate_id,
                                descriptor=eval_result.descriptor,
                                fitness=eval_result.full_score.scalar_fitness,
                                iteration=iteration,
                            )

                        self._record_iteration_metrics(
                            store=store,
                            state=state,
                            iteration=iteration,
                            islands=islands,
                            swarm=swarm,
                            active_island_id=island.policy.island_id,
                            candidate_id=candidate.candidate_id,
                            quick_fitness=eval_result.quick_score.scalar_fitness,
                            full_fitness=(eval_result.full_score.scalar_fitness if eval_result.full_score else None),
                            quick_median_us=self._benchmark_median_us(eval_result.quick_benchmark),
                            full_median_us=self._benchmark_median_us(eval_result.full_benchmark),
                            reason=("full_evaluated" if eval_result.full_score else "full_skipped_or_failed"),
                        )

                    now = time.time()
                    state.iteration = max(state.iteration, next_iteration)
                    last_checkpoint = self._checkpoint_if_due(
                        checkpoint_path=checkpoint_path,
                        state=state,
                        islands=islands,
                        candidates_by_id=candidates_by_id,
                        swarm=swarm,
                        now=now,
                        last_checkpoint=last_checkpoint,
                    )

                    inflight = proposal_futures or quick_eval_futures or full_eval_futures
                    pending = pending_quick or pending_full
                    budget_exhausted = self._submission_budget_exhausted(
                        next_iteration=next_iteration,
                        start_time=start_time,
                        swarm=swarm,
                    )
                    if budget_exhausted and not inflight and not pending:
                        break

                    if not completed_any and inflight:
                        wait(set(proposal_futures) | set(quick_eval_futures) | set(full_eval_futures), timeout=0.2, return_when=FIRST_COMPLETED)
                    elif not completed_any and not inflight and not pending:
                        break

            best_candidate_id, best_fitness = self._best_across_islands(islands)
            report_payload = {
                "run_id": state.run_id,
                "problem_id": problem.problem_id(),
                "iterations_completed": state.iteration,
                "accepted_updates": state.accepted_updates,
                "quick_scored": state.quick_scored,
                "full_scored": state.full_scored,
                "best_candidate_id": best_candidate_id,
                "best_fitness": best_fitness,
                "swarm_usage": to_dict(swarm.usage),
                "brev_instance": brev_instance,
                "islands": [
                    {
                        "island_id": island.policy.island_id,
                        "style": island.policy.style,
                        "mutation_scale": island.policy.mutation_scale,
                        "accepted_updates": island.accepted_updates,
                        "occupied_bins": island.archive.occupied_bins,
                        "coverage_ratio": island.archive.coverage_ratio(),
                        "top_elites": [
                            {
                                "candidate_id": cell.candidate_id,
                                "fitness": cell.fitness,
                                "bin_key": list(cell.bin_key),
                            }
                            for cell in island.archive.top_elites(5)
                        ],
                    }
                    for island in islands
                ],
            }
            report_path = run_dir / "reports" / "search_report.json"
            artifacts.write_json(report_path, report_payload)

            summary = RunSummary(
                run_id=state.run_id,
                problem_id=problem.problem_id(),
                total_candidates=len(candidates_by_id),
                quick_scored=state.quick_scored,
                full_scored=state.full_scored,
                best_candidate_id=best_candidate_id,
                best_fitness=best_fitness,
                report_path=str(report_path),
            )
            store.finalize_run(summary)
            artifacts.write_json(run_dir / "summary.json", summary)
            self._save_checkpoint(
                checkpoint_path=checkpoint_path,
                state=state,
                islands=islands,
                candidates_by_id=candidates_by_id,
                swarm=swarm,
            )
            return summary
        finally:
            store.close()

    def _seed_initial_population(
        self,
        *,
        problem: OptimizationProblem,
        state: SearchState,
        store: SQLiteStore,
        artifacts: ArtifactStore,
        islands: list[IslandState],
        swarm: SwarmAgentPool,
        candidates_by_id: dict[str, Candidate],
        seen_hashes: set[str],
        remote_client: RemoteEvaluatorClient | None,
        problem_config: dict[str, Any],
        island_quick_frontier: dict[str, float | None],
        prompt_context: dict[str, Any] | None = None,
    ) -> None:
        ctx = ProblemRunContext(run_id=state.run_id, seed=self.config.seed)
        baseline = problem.baseline(ctx)
        seeds = problem.seed_candidates(ctx)
        for idx, candidate in enumerate([baseline, *seeds]):
            if candidate is None:
                continue
            attach_content_hashes(candidate=candidate)
            if candidate.content_hash in seen_hashes:
                continue
            seen_hashes.add(candidate.content_hash)
            candidates_by_id[candidate.candidate_id] = candidate

            dummy_proposal = GeneratorDecision(
                candidate=candidate,
                changed_knobs={},
                expected_effect="seed",
                risk_level="low",
                rejected=False,
                used_llm=False,
                usage=None,
            )
            eval_result = self._evaluate_candidate(
                problem=problem,
                store=store,
                artifacts=artifacts,
                candidate=candidate,
                swarm=swarm,
                remote_client=remote_client,
                problem_config=problem_config,
                judge_input=dummy_proposal,
                prompt_context=prompt_context,
            )
            state.quick_scored += 1
            island = islands[idx % len(islands)]
            self._update_quick_frontier(
                frontier=island_quick_frontier,
                island_id=island.policy.island_id,
                quick_fitness=eval_result.quick_score.scalar_fitness,
            )

            if eval_result.descriptor is None:
                continue

            full = self._evaluate_full(
                problem=problem,
                store=store,
                artifacts=artifacts,
                candidate=candidate,
                prior_eval=eval_result,
                remote_client=remote_client,
                problem_config=problem_config,
            )
            if full is None:
                continue

            eval_result.full_score = full[0]
            eval_result.full_benchmark = full[1]
            state.full_scored += 1

            update = island.archive.insert(
                candidate_id=candidate.candidate_id,
                fitness=finite_fitness(eval_result.full_score.scalar_fitness),
                descriptor=eval_result.descriptor,
                iteration=state.iteration,
            )
            if update.accepted:
                island.accepted_updates += 1
                state.accepted_updates += 1

    def _evaluate_candidate(
        self,
        *,
        problem: OptimizationProblem,
        store: SQLiteStore,
        artifacts: ArtifactStore,
        candidate: Candidate,
        swarm: SwarmAgentPool,
        remote_client: RemoteEvaluatorClient | None,
        problem_config: dict[str, Any],
        judge_input: GeneratorDecision,
        prompt_context: dict[str, Any] | None = None,
    ) -> CandidateEvaluation:
        run_id = candidate.run_id
        cid = candidate.candidate_id

        store.save_candidate(candidate, CandidateState.PROPOSED)
        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=None,
            to_state=CandidateState.TRIAGED,
            reason="swarm triage",
        )
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "source") / "candidate.json",
            candidate,
        )
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "agent") / "generator_decision.json",
            judge_input,
        )

        static_local = problem.static_check(candidate)
        judge = swarm.next_judge().review(
            candidate=candidate, static=static_local, stage="triage",
            prompt_context=prompt_context,
        )
        swarm.usage.add(judge.usage)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "agent") / "judge_decision.json",
            judge,
        )

        if not judge.compile_worthy:
            score = ScoreRecord(
                run_id=run_id,
                candidate_id=cid,
                stage=BenchmarkStage.QUICK,
                scalar_fitness=-1e18,
                raw_score={"fitness": -1e18, "reason": "judge_rejected"},
            )
            attach_content_hashes(score_record=score)
            store.save_score(score)
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.TRIAGED,
                to_state=CandidateState.REJECTED_STATIC,
                reason="judge rejected candidate",
            )
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.REJECTED_STATIC,
                to_state=CandidateState.SCORED,
                reason="scored judge reject",
            )
            return CandidateEvaluation(
                candidate=candidate,
                static_check=static_local,
                judge=judge,
                build_result=None,
                validation_result=None,
                quick_benchmark=None,
                quick_score=score,
                descriptor=None,
            )

        remote_result: RemoteEvaluationResult | None = None
        if remote_client is not None:
            try:
                remote_result = remote_client.evaluate(
                    problem_id=problem.problem_id(),
                    candidate=candidate,
                    stage=BenchmarkStage.QUICK,
                    problem_config=problem_config,
                )
            except RemoteEvaluationError as exc:
                score = ScoreRecord(
                    run_id=run_id,
                    candidate_id=cid,
                    stage=BenchmarkStage.QUICK,
                    scalar_fitness=-1e18,
                    raw_score={"fitness": -1e18, "reason": "remote_eval_error", "error": str(exc)},
                )
                attach_content_hashes(score_record=score)
                store.save_score(score)
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "agent") / "remote_eval_error.json",
                    {"error": str(exc)},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.TRIAGED,
                    to_state=CandidateState.SCORED,
                    reason="remote eval unavailable",
                )
                return CandidateEvaluation(
                    candidate=candidate,
                    static_check=static_local,
                    judge=judge,
                    build_result=None,
                    validation_result=None,
                    quick_benchmark=None,
                    quick_score=score,
                    descriptor=None,
                )

        static = remote_result.static_check if remote_result else static_local
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "triage") / "static_check.json",
            static,
        )
        if not static.ok:
            score = ScoreRecord(
                run_id=run_id,
                candidate_id=cid,
                stage=BenchmarkStage.QUICK,
                scalar_fitness=-1e18,
                raw_score={"fitness": -1e18, "reason": "static_check_failed"},
            )
            attach_content_hashes(score_record=score)
            store.save_score(score)
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.TRIAGED,
                to_state=CandidateState.REJECTED_STATIC,
                reason="static check failed",
            )
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.REJECTED_STATIC,
                to_state=CandidateState.SCORED,
                reason="scored static reject",
            )
            return CandidateEvaluation(
                candidate=candidate,
                static_check=static,
                judge=judge,
                build_result=None,
                validation_result=None,
                quick_benchmark=None,
                quick_score=score,
                descriptor=None,
            )

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.TRIAGED,
            to_state=CandidateState.QUEUED_BUILD,
            reason="queued build",
        )

        build_exec: BuildExecution | None = None
        if remote_result and remote_result.build_result is not None:
            build_result = remote_result.build_result
        else:
            build_exec = problem.build(candidate)
            build_result = build_exec.result
            attach_content_hashes(build_result=build_result)

        store.save_build_result(build_result)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "build") / "build_result.json",
            build_result,
        )
        if build_result.status is not BuildStatus.SUCCESS:
            score = ScoreRecord(
                run_id=run_id,
                candidate_id=cid,
                stage=BenchmarkStage.QUICK,
                scalar_fitness=-1e18,
                raw_score={"fitness": -1e18, "reason": "build_failed"},
            )
            attach_content_hashes(score_record=score)
            store.save_score(score)
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.QUEUED_BUILD,
                to_state=CandidateState.BUILD_FAILED,
                reason="build failed",
            )
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.BUILD_FAILED,
                to_state=CandidateState.SCORED,
                reason="scored build failure",
            )
            return CandidateEvaluation(
                candidate=candidate,
                static_check=static,
                judge=judge,
                build_result=build_result,
                validation_result=None,
                quick_benchmark=None,
                quick_score=score,
                descriptor=None,
            )

        if remote_result and remote_result.validation_result is not None:
            validation = remote_result.validation_result
        else:
            if build_exec is None:
                build_exec = problem.build(candidate)
            validation = problem.validate(candidate, build_exec)
            attach_content_hashes(validation_result=validation)

        store.save_validation_result(validation)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "validation") / "validation_result.json",
            validation,
        )
        if validation.status is not ValidationStatus.PASS:
            raw_score = problem.score(self._error_benchmark(candidate, BenchmarkStage.QUICK), validation)
            scalar = scalarize_raw_score(raw_score)
            score = ScoreRecord(
                run_id=run_id,
                candidate_id=cid,
                stage=BenchmarkStage.QUICK,
                scalar_fitness=scalar,
                raw_score=raw_score,
            )
            attach_content_hashes(score_record=score)
            store.save_score(score)
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.QUEUED_BUILD,
                to_state=CandidateState.INVALID,
                reason="validation failed",
            )
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.INVALID,
                to_state=CandidateState.SCORED,
                reason="scored validation failure",
            )
            return CandidateEvaluation(
                candidate=candidate,
                static_check=static,
                judge=judge,
                build_result=build_result,
                validation_result=validation,
                quick_benchmark=None,
                quick_score=score,
                descriptor=None,
                build_execution=build_exec,
            )

        if remote_result and remote_result.benchmark_result is not None:
            benchmark = remote_result.benchmark_result
            raw_score = remote_result.raw_score if remote_result.raw_score is not None else problem.score(benchmark, validation)
            scalar = (
                float(remote_result.scalar_fitness)
                if remote_result.scalar_fitness is not None
                else scalarize_raw_score(raw_score)
            )
            descriptor = remote_result.descriptor
        else:
            if build_exec is None:
                build_exec = problem.build(candidate)
            benchmark = problem.benchmark(candidate, build_exec, BenchmarkStage.QUICK)
            attach_content_hashes(benchmark_result=benchmark)
            raw_score = problem.score(benchmark, validation)
            scalar = scalarize_raw_score(raw_score)
            descriptor = problem.describe(candidate, build_exec, benchmark)

        attach_content_hashes(benchmark_result=benchmark)
        store.save_benchmark_result(benchmark)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "bench_quick") / "benchmark_result.json",
            benchmark,
        )

        score = ScoreRecord(
            run_id=run_id,
            candidate_id=cid,
            stage=BenchmarkStage.QUICK,
            scalar_fitness=finite_fitness(scalar),
            raw_score=raw_score,
        )
        attach_content_hashes(score_record=score)
        store.save_score(score)

        if descriptor is not None:
            attach_content_hashes(descriptor=descriptor)
            store.save_descriptor(descriptor)
            artifacts.write_json(
                artifacts.candidate_dir(run_id, cid, "descriptor") / "descriptor.json",
                descriptor,
            )

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.QUEUED_BUILD,
            to_state=CandidateState.SCORED,
            reason="quick scored",
        )
        return CandidateEvaluation(
            candidate=candidate,
            static_check=static,
            judge=judge,
            build_result=build_result,
            validation_result=validation,
            quick_benchmark=benchmark,
            quick_score=score,
            descriptor=descriptor,
            build_execution=build_exec,
        )

    def _evaluate_full(
        self,
        *,
        problem: OptimizationProblem,
        store: SQLiteStore,
        artifacts: ArtifactStore,
        candidate: Candidate,
        prior_eval: CandidateEvaluation,
        remote_client: RemoteEvaluatorClient | None,
        problem_config: dict[str, Any],
    ) -> tuple[ScoreRecord, BenchmarkResult] | None:
        run_id = candidate.run_id
        cid = candidate.candidate_id
        validation = prior_eval.validation_result
        if validation is None or validation.status is not ValidationStatus.PASS:
            return None

        if remote_client is not None:
            try:
                remote = remote_client.evaluate(
                    problem_id=problem.problem_id(),
                    candidate=candidate,
                    stage=BenchmarkStage.FULL,
                    problem_config=problem_config,
                )
            except RemoteEvaluationError:
                return None
            if remote.benchmark_result is None:
                return None
            benchmark = remote.benchmark_result
            raw_score = remote.raw_score if remote.raw_score is not None else problem.score(benchmark, validation)
            scalar = (
                float(remote.scalar_fitness)
                if remote.scalar_fitness is not None
                else scalarize_raw_score(raw_score)
            )
        else:
            build_exec = prior_eval.build_execution
            if build_exec is None:
                return None
            benchmark = problem.benchmark(candidate, build_exec, BenchmarkStage.FULL)
            raw_score = problem.score(benchmark, validation)
            scalar = scalarize_raw_score(raw_score)

        attach_content_hashes(benchmark_result=benchmark)
        store.save_benchmark_result(benchmark)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "bench_full") / "benchmark_result.json",
            benchmark,
        )

        score = ScoreRecord(
            run_id=run_id,
            candidate_id=cid,
            stage=BenchmarkStage.FULL,
            scalar_fitness=finite_fitness(scalar),
            raw_score=raw_score,
        )
        attach_content_hashes(score_record=score)
        store.save_score(score)
        return score, benchmark

    @staticmethod
    def _error_benchmark(candidate: Candidate, stage: BenchmarkStage) -> BenchmarkResult:
        return BenchmarkResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            stage=stage,
            status=BenchmarkStatus.ERROR,
            samples=0,
            warmup_iters=0,
            timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
            env={},
            profile={},
        )

    def _should_run_full(self, *, quick_fitness: float, quick_baseline: float | None) -> bool:
        fitness = finite_fitness(float(quick_fitness))
        if fitness <= -1e17:
            return False
        if quick_baseline is None:
            return True
        baseline = finite_fitness(float(quick_baseline))
        if baseline <= -1e17:
            return True
        if baseline <= 0:
            return fitness > baseline
        return fitness >= (baseline * self.config.full_trigger_ratio)

    def _select_parent_candidate(
        self,
        island: IslandState,
        candidates_by_id: dict[str, Candidate],
    ) -> Candidate | None:
        cid = island.select_parent(self._rng)
        if cid and cid in candidates_by_id:
            return candidates_by_id[cid]
        if not candidates_by_id:
            return None
        return self._rng.choice(list(candidates_by_id.values()))

    def _best_across_islands(self, islands: list[IslandState]) -> tuple[str | None, float | None]:
        best_id: str | None = None
        best_fitness: float | None = None
        for island in islands:
            for cell in island.archive.top_elites(1):
                if best_fitness is None or cell.fitness > best_fitness:
                    best_fitness = cell.fitness
                    best_id = cell.candidate_id
        return best_id, best_fitness

    def _migration_due(self, accepted_updates: int) -> bool:
        interval = max(1, self.config.migration_every_updates)
        return accepted_updates > 0 and accepted_updates % interval == 0

    def _checkpoint_due(self, iteration: int, elapsed_s: float) -> bool:
        iter_due = iteration > 0 and iteration % max(1, self.config.checkpoint_every_iterations) == 0
        time_due = elapsed_s >= max(1.0, self.config.checkpoint_every_seconds)
        return iter_due or time_due

    def _checkpoint_if_due(
        self,
        *,
        checkpoint_path: Path,
        state: SearchState,
        islands: list[IslandState],
        candidates_by_id: dict[str, Candidate],
        swarm: SwarmAgentPool,
        now: float,
        last_checkpoint: float,
    ) -> float:
        if self._checkpoint_due(state.iteration, now - last_checkpoint):
            self._save_checkpoint(
                checkpoint_path=checkpoint_path,
                state=state,
                islands=islands,
                candidates_by_id=candidates_by_id,
                swarm=swarm,
            )
            return now
        return last_checkpoint

    def _save_checkpoint(
        self,
        *,
        checkpoint_path: Path,
        state: SearchState,
        islands: list[IslandState],
        candidates_by_id: dict[str, Candidate],
        swarm: SwarmAgentPool,
    ) -> None:
        payload = {
            "schema_version": "v1",
            "run_id": state.run_id,
            "iteration": state.iteration,
            "accepted_updates": state.accepted_updates,
            "quick_scored": state.quick_scored,
            "full_scored": state.full_scored,
            "rng_state_b64": self._encode_rng_state(),
            "swarm_usage": to_dict(swarm.usage),
            "islands": [
                {
                    "policy": to_dict(island.policy),
                    "accepted_updates": island.accepted_updates,
                    "imported_parent_ids": list(island.imported_parent_ids),
                    "archive": island.archive.export_state(),
                }
                for island in islands
            ],
            "candidates": {cid: to_dict(candidate) for cid, candidate in candidates_by_id.items()},
        }
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")

    def _load_checkpoint(self, checkpoint_path: Path) -> dict[str, Any] | None:
        if not checkpoint_path.exists():
            return None
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))

    def _restore_islands(self, rows: list[dict[str, Any]]) -> list[IslandState]:
        out: list[IslandState] = []
        for row in rows:
            policy_row = row.get("policy", {})
            policy = default_island_policies()[0]
            for candidate in default_island_policies():
                if candidate.island_id == policy_row.get("island_id"):
                    policy = candidate
                    break
            # Preserve style fields from checkpoint if present.
            policy = type(policy)(
                island_id=str(policy_row.get("island_id", policy.island_id)),
                style=str(policy_row.get("style", policy.style)),
                mutation_scale=float(policy_row.get("mutation_scale", policy.mutation_scale)),
            )
            archive = MapElitesArchive.from_state(dict(row.get("archive", {})))
            out.append(
                IslandState(
                    policy=policy,
                    archive=archive,
                    accepted_updates=int(row.get("accepted_updates", 0)),
                    imported_parent_ids=[str(x) for x in row.get("imported_parent_ids", [])],
                )
            )
        return out if out else self._init_islands()

    def _init_islands(self) -> list[IslandState]:
        islands: list[IslandState] = []
        for policy in default_island_policies():
            archive = MapElitesArchive(axes=self.config.descriptor_axes)
            islands.append(IslandState(policy=policy, archive=archive))
        return islands

    def _encode_rng_state(self) -> str:
        return base64.b64encode(pickle.dumps(self._rng.getstate())).decode("ascii")

    def _restore_rng_state(self, payload: dict[str, Any]) -> None:
        encoded = payload.get("rng_state_b64")
        if not encoded:
            return
        data = base64.b64decode(encoded)
        state = pickle.loads(data)  # noqa: S301
        self._rng.setstate(state)

    def _resolve_checkpoint_path(self) -> Path:
        if self.config.checkpoint_path is not None:
            return self.config.checkpoint_path
        return self.workspace / "checkpoints" / "latest.json"

    @staticmethod
    def _benchmark_median_us(benchmark: BenchmarkResult | None) -> float | None:
        if benchmark is None:
            return None
        if benchmark.status is not BenchmarkStatus.SUCCESS:
            return None
        return float(benchmark.timing.median_us)

    @staticmethod
    def _update_quick_frontier(
        *,
        frontier: dict[str, float | None],
        island_id: str,
        quick_fitness: float | None,
    ) -> None:
        if quick_fitness is None:
            return
        fitness = finite_fitness(float(quick_fitness))
        if fitness <= -1e17:
            return
        current = frontier.get(island_id)
        if current is None or fitness > current:
            frontier[island_id] = fitness

    def _remote_eval_urls(self) -> list[str]:
        raw = (self.config.remote_eval_url or "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _select_remote_client(
        self,
        remote_clients: list[RemoteEvaluatorClient],
        candidate: Candidate,
    ) -> RemoteEvaluatorClient | None:
        if not remote_clients:
            return None
        if len(remote_clients) == 1:
            return remote_clients[0]
        key = (candidate.content_hash or candidate.candidate_id or "").encode("utf-8")
        if not key:
            return remote_clients[0]
        score = 0
        for idx, value in enumerate(key):
            score += (idx + 1) * value
        return remote_clients[score % len(remote_clients)]

    def _submission_budget_exhausted(
        self,
        *,
        next_iteration: int,
        start_time: float,
        swarm: SwarmAgentPool,
    ) -> bool:
        if next_iteration >= self.config.max_iterations:
            return True
        if (time.time() - start_time) >= max(1.0, self.config.max_minutes * 60.0):
            return True
        if self.config.token_budget > 0 and swarm.usage.total_tokens >= self.config.token_budget:
            return True
        return False

    def _apply_archive_update(
        self,
        *,
        state: SearchState,
        island: IslandState,
        islands: list[IslandState],
        candidates_by_id: dict[str, Candidate],
        artifacts: ArtifactStore,
        run_dir: Path,
        candidate_id: str,
        descriptor: Descriptor | None,
        fitness: float,
        iteration: int,
    ) -> None:
        if descriptor is None:
            return
        update = island.archive.insert(
            candidate_id=candidate_id,
            fitness=finite_fitness(fitness),
            descriptor=descriptor,
            iteration=iteration,
        )
        if not update.accepted:
            return
        island.accepted_updates += 1
        state.accepted_updates += 1
        if not self._migration_due(state.accepted_updates):
            return
        migrations = migrate_ring(
            islands,
            packet_size=self.config.migration_packet_size,
            candidate_by_id=candidates_by_id,
        )
        if not migrations:
            return
        artifacts.write_json(
            run_dir / "migrations" / f"iter_{iteration:06d}.json",
            {
                "iteration": iteration,
                "accepted_updates": state.accepted_updates,
                "migrations": migrations,
            },
        )

    def _record_internal_eval_error(
        self,
        *,
        store: SQLiteStore,
        artifacts: ArtifactStore,
        candidate: Candidate,
        reason: str,
    ) -> CandidateEvaluation:
        score = ScoreRecord(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            stage=BenchmarkStage.QUICK,
            scalar_fitness=-1e18,
            raw_score={"fitness": -1e18, "reason": reason},
        )
        attach_content_hashes(score_record=score)
        store.save_candidate(candidate, CandidateState.PROPOSED)
        store.save_score(score)
        store.transition_state(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            from_state=None,
            to_state=CandidateState.DEAD_LETTER,
            reason=reason,
        )
        artifacts.write_json(
            artifacts.candidate_dir(candidate.run_id, candidate.candidate_id, "agent") / "internal_eval_error.json",
            {"reason": reason},
        )
        return CandidateEvaluation(
            candidate=candidate,
            static_check=StaticCheckResult(candidate_id=candidate.candidate_id, ok=False, reasons=[reason]),
            judge=None,
            build_result=None,
            validation_result=None,
            quick_benchmark=None,
            quick_score=score,
            descriptor=None,
        )

    def _record_iteration_metrics(
        self,
        *,
        store: SQLiteStore,
        state: SearchState,
        iteration: int,
        islands: list[IslandState],
        swarm: SwarmAgentPool,
        active_island_id: str,
        candidate_id: str | None,
        quick_fitness: float | None,
        full_fitness: float | None,
        quick_median_us: float | None,
        full_median_us: float | None,
        reason: str,
    ) -> None:
        global_best_candidate_id, global_best_fitness = self._best_across_islands(islands)
        metrics: list[IterationMetric] = []
        for island in islands:
            top = island.archive.top_elites(1)
            island_top_fitness = float(top[0].fitness) if top else None

            is_active = island.policy.island_id == active_island_id
            metric = IterationMetric(
                run_id=state.run_id,
                iteration=iteration,
                island_id=island.policy.island_id,
                candidate_id=(candidate_id if is_active else None),
                quick_fitness=(quick_fitness if is_active else None),
                full_fitness=(full_fitness if is_active else None),
                quick_median_us=(quick_median_us if is_active else None),
                full_median_us=(full_median_us if is_active else None),
                island_top_fitness=island_top_fitness,
                island_coverage_ratio=island.archive.coverage_ratio(),
                island_occupied_bins=island.archive.occupied_bins,
                island_accepted_updates=island.accepted_updates,
                global_best_candidate_id=global_best_candidate_id,
                global_best_fitness=global_best_fitness,
                total_tokens=swarm.usage.total_tokens,
                payload={
                    "reason": reason,
                    "active_island_id": active_island_id,
                    "imported_parent_queue_len": len(island.imported_parent_ids),
                },
            )
            attach_content_hashes(iteration_metric=metric)
            metrics.append(metric)
        store.save_iteration_metrics(metrics)

    @staticmethod
    def _extract_failure_hint(eval_result: CandidateEvaluation) -> str | None:
        """Extract a short error summary from a failed evaluation."""
        # Validation failures carry the richest info (compilation_error=..., correctness_issue=...).
        vr = eval_result.validation_result
        if vr is not None and vr.failing_cases:
            return vr.failing_cases[0].summary[:200]
        # Raw score reason (judge_rejected, build_failed, static_check_failed, remote_eval_error).
        raw = eval_result.quick_score.raw_score
        if isinstance(raw, dict):
            reason = raw.get("reason", "")
            error = raw.get("error", "")
            if error:
                return f"{reason}: {error}"[:200]
            if reason:
                return str(reason)[:200]
        return None

    @staticmethod
    def _get_prompt_context(problem: OptimizationProblem) -> dict[str, Any] | None:
        fn = getattr(problem, "generator_prompt_context", None)
        if callable(fn):
            ctx = fn()
            if isinstance(ctx, dict) and ctx:
                return ctx
        return None

    @staticmethod
    def _problem_config(problem: OptimizationProblem) -> dict[str, Any]:
        to_config = getattr(problem, "to_config_dict", None)
        if callable(to_config):
            config = to_config()
            if isinstance(config, dict):
                return dict(config)
        return {}

    def _build_llm_client(self) -> NemotronClient | None:
        if not self.config.llm_enabled:
            return None
        config = NemotronConfig(
            provider=self.config.nemotron_provider,
            model=self.config.nemotron_model,
            base_url=self.config.nemotron_base_url,
            api_key=self.config.nemotron_api_key,
            api_key_env=self.config.nemotron_api_key_env,
            max_concurrent_requests=self.config.nemotron_max_concurrent_requests,
        )
        # Validate key availability up front.
        config.resolved_api_key()
        return NemotronClient(config)

    def _ensure_brev_instance(self) -> dict[str, Any]:
        client = BrevClient()
        try:
            instance = client.ensure_instance(
                name=str(self.config.brev.instance_name),
                machine=self.config.brev.machine,
                create_if_missing=self.config.brev.create_if_missing,
                wait_timeout_s=self.config.brev.wait_timeout_s,
            )
        except BrevError as exc:
            raise RuntimeError(f"brev preflight failed: {exc}") from exc
        return {
            "name": instance.name,
            "status": instance.status,
            "shell": instance.shell,
            "instance_id": instance.instance_id,
            "machine": instance.machine,
        }
