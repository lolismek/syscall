from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .artifacts import ArtifactStore
from .hashing import attach_content_hashes
from .manifest import build_run_manifest
from .models import (
    BenchmarkResult,
    BenchmarkStage,
    BenchmarkStatus,
    BenchmarkTiming,
    BuildExecution,
    BuildStatus,
    Candidate,
    CandidateState,
    Descriptor,
    RetryPolicy,
    RunSummary,
    ScoreRecord,
    ValidationResult,
    ValidationStatus,
)
from .persistence import SQLiteStore
from .remote import RemoteEvaluationError, RemoteEvaluationResult, RemoteEvaluatorClient
from .sdk import OptimizationProblem, ProblemRunContext
from .serialization import to_dict


@dataclass(slots=True)
class PipelineConfig:
    workspace: Path
    seed: int = 42
    full_benchmark_top_k: int = 2
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    remote_eval_url: str | None = None
    remote_eval_timeout_s: float = 120.0


@dataclass(slots=True)
class _CandidateEval:
    candidate: Candidate
    build: BuildExecution | None = None
    validation: ValidationResult | None = None
    quick_benchmark: BenchmarkResult | None = None
    quick_score: ScoreRecord | None = None
    full_benchmark: BenchmarkResult | None = None
    full_score: ScoreRecord | None = None
    descriptor: Descriptor | None = None
    terminal_state: CandidateState = CandidateState.SCORED


class SingleWorkerPipeline:
    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.workspace = config.workspace
        self.db_path = self.workspace / "db" / "runs.sqlite"
        self.artifacts_root = self.workspace / "artifacts"

    def run(self, problem: OptimizationProblem) -> RunSummary:
        run_id = str(uuid4())
        self.workspace.mkdir(parents=True, exist_ok=True)

        store = SQLiteStore(self.db_path)
        artifacts = ArtifactStore(self.artifacts_root)
        run_ctx = ProblemRunContext(run_id=run_id, seed=self.config.seed)
        remote_client = None
        problem_config = self._problem_config(problem)
        if self.config.remote_eval_url:
            remote_client = RemoteEvaluatorClient(
                self.config.remote_eval_url,
                timeout_s=self.config.remote_eval_timeout_s,
            )

        try:
            manifest = build_run_manifest(
                run_id=run_id,
                problem_id=problem.problem_id(),
                seed=self.config.seed,
                repo_root=Path.cwd(),
            )
            store.start_run(
                run_id=run_id,
                problem_id=problem.problem_id(),
                manifest=manifest,
                config=to_dict(self.config),
            )
            artifacts.write_json(artifacts.run_dir(run_id) / "manifest.json", manifest)

            candidates = self._collect_candidates(problem, run_ctx)
            evals: list[_CandidateEval] = []
            for candidate in candidates:
                evals.append(
                    self._run_quick_phase(
                        problem,
                        store,
                        artifacts,
                        candidate,
                        remote_client,
                        problem_config,
                    )
                )

            quick_ranked = [
                item
                for item in sorted(
                    evals,
                    key=lambda item: item.quick_score.scalar_fitness if item.quick_score else float("-inf"),
                    reverse=True,
                )
                if item.validation and item.validation.status is ValidationStatus.PASS
            ]

            top_k = max(0, self.config.full_benchmark_top_k)
            for item in quick_ranked[:top_k]:
                self._run_full_phase(problem, store, artifacts, item, remote_client, problem_config)

            for item in evals:
                if item.descriptor is None and item.build and item.quick_benchmark:
                    descriptor = problem.describe(item.candidate, item.build, item.quick_benchmark)
                    attach_content_hashes(descriptor=descriptor)
                    item.descriptor = descriptor
                    store.save_descriptor(descriptor)
                    artifacts.write_json(
                        artifacts.candidate_dir(run_id, item.candidate.candidate_id, "descriptor")
                        / "descriptor.json",
                        descriptor,
                    )

                if item.terminal_state is CandidateState.SCORED:
                    store.transition_state(
                        run_id=run_id,
                        candidate_id=item.candidate.candidate_id,
                        from_state=CandidateState.SCORED,
                        to_state=CandidateState.ARCHIVED,
                        reason="candidate archived",
                    )

            full_scores = [item.full_score for item in evals if item.full_score]
            quick_scores = [item.quick_score for item in evals if item.quick_score]
            best_record = None
            if full_scores:
                best_record = max(full_scores, key=lambda x: x.scalar_fitness)
            elif quick_scores:
                best_record = max(quick_scores, key=lambda x: x.scalar_fitness)

            report_payload = {
                "run_id": run_id,
                "problem_id": problem.problem_id(),
                "seed": self.config.seed,
                "total_candidates": len(evals),
                "quick_scores": [to_dict(item.quick_score) for item in evals if item.quick_score],
                "full_scores": [to_dict(item.full_score) for item in evals if item.full_score],
                "best_candidate_id": best_record.candidate_id if best_record else None,
                "best_fitness": best_record.scalar_fitness if best_record else None,
            }
            report_path = artifacts.run_dir(run_id) / "reports" / "run_report.json"
            artifacts.write_json(report_path, report_payload)

            summary = RunSummary(
                run_id=run_id,
                problem_id=problem.problem_id(),
                total_candidates=len(evals),
                quick_scored=len(quick_scores),
                full_scored=len(full_scores),
                best_candidate_id=best_record.candidate_id if best_record else None,
                best_fitness=best_record.scalar_fitness if best_record else None,
                report_path=str(report_path),
            )
            store.finalize_run(summary)
            artifacts.write_json(artifacts.run_dir(run_id) / "summary.json", summary)
            return summary
        finally:
            store.close()

    def _collect_candidates(
        self,
        problem: OptimizationProblem,
        ctx: ProblemRunContext,
    ) -> list[Candidate]:
        baseline = problem.baseline(ctx)
        seeds = problem.seed_candidates(ctx)

        out: list[Candidate] = []
        seen_hashes: set[str] = set()
        for candidate in [baseline, *seeds]:
            if candidate is None:
                continue
            attach_content_hashes(candidate=candidate)
            if candidate.content_hash in seen_hashes:
                continue
            seen_hashes.add(candidate.content_hash)
            out.append(candidate)
        return out

    def _run_quick_phase(
        self,
        problem: OptimizationProblem,
        store: SQLiteStore,
        artifacts: ArtifactStore,
        candidate: Candidate,
        remote_client: RemoteEvaluatorClient | None,
        problem_config: dict[str, object],
    ) -> _CandidateEval:
        run_id = candidate.run_id
        cid = candidate.candidate_id

        store.save_candidate(candidate, CandidateState.PROPOSED)
        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=None,
            to_state=CandidateState.TRIAGED,
            reason="entered triage",
        )
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "source") / "candidate.json",
            candidate,
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
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error.json",
                    {"error": str(exc)},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.TRIAGED,
                    to_state=CandidateState.DEAD_LETTER,
                    reason=f"remote eval error: {exc}",
                )
                score = ScoreRecord(
                    run_id=run_id,
                    candidate_id=cid,
                    stage=BenchmarkStage.QUICK,
                    scalar_fitness=-1e18,
                    raw_score={"fitness": -1e18, "reason": "remote_eval_error"},
                )
                attach_content_hashes(score_record=score)
                store.save_score(score)
                return _CandidateEval(
                    candidate=candidate,
                    quick_score=score,
                    terminal_state=CandidateState.DEAD_LETTER,
                )

        static = remote_result.static_check if remote_result else problem.static_check(candidate)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "triage") / "static_check.json",
            static,
        )

        if not static.ok:
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.TRIAGED,
                to_state=CandidateState.REJECTED_STATIC,
                reason="; ".join(static.reasons),
            )
            score = ScoreRecord(
                run_id=run_id,
                candidate_id=cid,
                stage=BenchmarkStage.QUICK,
                scalar_fitness=(
                    remote_result.scalar_fitness
                    if remote_result and remote_result.scalar_fitness is not None
                    else -1e18
                ),
                raw_score=(
                    remote_result.raw_score
                    if remote_result and remote_result.raw_score is not None
                    else {"fitness": -1e18, "reason": "static_check_failed"}
                ),
            )
            attach_content_hashes(score_record=score)
            store.save_score(score)
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.REJECTED_STATIC,
                to_state=CandidateState.SCORED,
                reason="scored static reject",
            )
            return _CandidateEval(candidate=candidate, quick_score=score)

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.TRIAGED,
            to_state=CandidateState.QUEUED_BUILD,
            reason="queued for build",
        )

        build: BuildExecution | None
        if remote_result:
            build = None
            if remote_result.build_result is None:
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error.json",
                    {"error": "remote response missing build_result"},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.QUEUED_BUILD,
                    to_state=CandidateState.DEAD_LETTER,
                    reason="remote response missing build_result",
                )
                score = ScoreRecord(
                    run_id=run_id,
                    candidate_id=cid,
                    stage=BenchmarkStage.QUICK,
                    scalar_fitness=-1e18,
                    raw_score={"fitness": -1e18, "reason": "remote_response_invalid"},
                )
                attach_content_hashes(score_record=score)
                store.save_score(score)
                return _CandidateEval(
                    candidate=candidate,
                    quick_score=score,
                    terminal_state=CandidateState.DEAD_LETTER,
                )
            build_result = remote_result.build_result
        else:
            build = self._build_with_retry(problem, candidate)
            build_result = build.result

        attach_content_hashes(build_result=build_result)
        store.save_build_result(build_result)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "build") / "build_result.json",
            build_result,
        )

        if build_result.status is not BuildStatus.SUCCESS:
            from_state = CandidateState.QUEUED_BUILD
            target_state = CandidateState.BUILD_FAILED
            reason = f"build status={build_result.status.value}"
            if build_result.status in {BuildStatus.INFRA_ERROR, BuildStatus.TIMEOUT}:
                target_state = CandidateState.DEAD_LETTER
                reason = f"build exhausted retries with status={build_result.status.value}"
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=from_state,
                to_state=target_state,
                reason=reason,
            )
            score = ScoreRecord(
                run_id=run_id,
                candidate_id=cid,
                stage=BenchmarkStage.QUICK,
                scalar_fitness=-1e18,
                raw_score={"fitness": -1e18, "reason": "build_failed"},
            )
            attach_content_hashes(score_record=score)
            store.save_score(score)
            if target_state is not CandidateState.DEAD_LETTER:
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=target_state,
                    to_state=CandidateState.SCORED,
                    reason="scored build failure",
                )
            terminal_state = CandidateState.DEAD_LETTER if target_state is CandidateState.DEAD_LETTER else CandidateState.SCORED
            return _CandidateEval(
                candidate=candidate,
                build=build,
                quick_score=score,
                terminal_state=terminal_state,
            )

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.QUEUED_BUILD,
            to_state=CandidateState.QUEUED_VALIDATE,
            reason="build succeeded",
        )
        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.QUEUED_VALIDATE,
            to_state=CandidateState.VALIDATING,
            reason="validation started",
        )

        if remote_result:
            if remote_result.validation_result is None:
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error.json",
                    {"error": "remote response missing validation_result"},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.VALIDATING,
                    to_state=CandidateState.DEAD_LETTER,
                    reason="remote response missing validation_result",
                )
                score = ScoreRecord(
                    run_id=run_id,
                    candidate_id=cid,
                    stage=BenchmarkStage.QUICK,
                    scalar_fitness=-1e18,
                    raw_score={"fitness": -1e18, "reason": "remote_response_invalid"},
                )
                attach_content_hashes(score_record=score)
                store.save_score(score)
                return _CandidateEval(
                    candidate=candidate,
                    build=build,
                    quick_score=score,
                    terminal_state=CandidateState.DEAD_LETTER,
                )
            validation = remote_result.validation_result
        else:
            if build is None:
                raise RuntimeError("local build execution missing")
            validation = problem.validate(candidate, build)
        attach_content_hashes(validation_result=validation)
        store.save_validation_result(validation)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "validation") / "validation_result.json",
            validation,
        )

        if validation.status is not ValidationStatus.PASS:
            store.transition_state(
                run_id=run_id,
                candidate_id=cid,
                from_state=CandidateState.VALIDATING,
                to_state=CandidateState.INVALID,
                reason=f"validation status={validation.status.value}",
            )
            raw_score = (
                remote_result.raw_score
                if remote_result and remote_result.raw_score is not None
                else problem.score(self._error_benchmark_result(candidate, BenchmarkStage.QUICK), validation)
            )
            # Build a deterministic fallback for invalid candidates.
            scalar = (
                remote_result.scalar_fitness
                if remote_result and remote_result.scalar_fitness is not None
                else self._scalarize(raw_score)
            )
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
                from_state=CandidateState.INVALID,
                to_state=CandidateState.SCORED,
                reason="scored invalid candidate",
            )
            return _CandidateEval(candidate=candidate, build=build, validation=validation, quick_score=score)

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.VALIDATING,
            to_state=CandidateState.QUEUED_BENCH_QUICK,
            reason="queued quick benchmark",
        )

        if remote_result:
            if remote_result.benchmark_result is None:
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error.json",
                    {"error": "remote response missing benchmark_result"},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.QUEUED_BENCH_QUICK,
                    to_state=CandidateState.DEAD_LETTER,
                    reason="remote response missing benchmark_result",
                )
                score = ScoreRecord(
                    run_id=run_id,
                    candidate_id=cid,
                    stage=BenchmarkStage.QUICK,
                    scalar_fitness=-1e18,
                    raw_score={"fitness": -1e18, "reason": "remote_response_invalid"},
                )
                attach_content_hashes(score_record=score)
                store.save_score(score)
                return _CandidateEval(
                    candidate=candidate,
                    build=build,
                    validation=validation,
                    quick_score=score,
                    terminal_state=CandidateState.DEAD_LETTER,
                )
            bench_quick = remote_result.benchmark_result
        else:
            if build is None:
                raise RuntimeError("local build execution missing")
            bench_quick = self._benchmark_with_retry(problem, candidate, build, BenchmarkStage.QUICK)
        attach_content_hashes(benchmark_result=bench_quick)
        store.save_benchmark_result(bench_quick)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "benchmark") / "quick.json",
            bench_quick,
        )

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.QUEUED_BENCH_QUICK,
            to_state=CandidateState.BENCH_QUICK_DONE,
            reason=f"quick benchmark status={bench_quick.status.value}",
        )

        raw_score = (
            remote_result.raw_score
            if remote_result and remote_result.raw_score is not None
            else problem.score(bench_quick, validation)
        )
        quick_score = ScoreRecord(
            run_id=run_id,
            candidate_id=cid,
            stage=BenchmarkStage.QUICK,
            scalar_fitness=(
                remote_result.scalar_fitness
                if remote_result and remote_result.scalar_fitness is not None
                else self._scalarize(raw_score)
            ),
            raw_score=raw_score,
        )
        attach_content_hashes(score_record=quick_score)
        store.save_score(quick_score)
        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.BENCH_QUICK_DONE,
            to_state=CandidateState.SCORED,
            reason="quick score saved",
        )

        descriptor = None
        if remote_result and remote_result.descriptor is not None:
            descriptor = remote_result.descriptor
            attach_content_hashes(descriptor=descriptor)
            store.save_descriptor(descriptor)
            artifacts.write_json(
                artifacts.candidate_dir(run_id, cid, "descriptor") / "descriptor.json",
                descriptor,
            )

        return _CandidateEval(
            candidate=candidate,
            build=build,
            validation=validation,
            quick_benchmark=bench_quick,
            quick_score=quick_score,
            descriptor=descriptor,
        )

    def _run_full_phase(
        self,
        problem: OptimizationProblem,
        store: SQLiteStore,
        artifacts: ArtifactStore,
        item: _CandidateEval,
        remote_client: RemoteEvaluatorClient | None,
        problem_config: dict[str, object],
    ) -> None:
        candidate = item.candidate
        run_id = candidate.run_id
        cid = candidate.candidate_id

        if item.validation is None:
            return

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.SCORED,
            to_state=CandidateState.QUEUED_BENCH_FULL,
            reason="selected for full benchmark",
        )

        remote_result: RemoteEvaluationResult | None = None
        if remote_client is not None:
            try:
                remote_result = remote_client.evaluate(
                    problem_id=problem.problem_id(),
                    candidate=candidate,
                    stage=BenchmarkStage.FULL,
                    problem_config=problem_config,
                )
            except RemoteEvaluationError as exc:
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error_full.json",
                    {"error": str(exc)},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.QUEUED_BENCH_FULL,
                    to_state=CandidateState.DEAD_LETTER,
                    reason=f"remote full eval error: {exc}",
                )
                item.terminal_state = CandidateState.DEAD_LETTER
                return

        if remote_result:
            if remote_result.benchmark_result is None:
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error_full.json",
                    {"error": "remote response missing benchmark_result"},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.QUEUED_BENCH_FULL,
                    to_state=CandidateState.DEAD_LETTER,
                    reason="remote response missing benchmark_result",
                )
                item.terminal_state = CandidateState.DEAD_LETTER
                return

            if remote_result.build_result is not None:
                attach_content_hashes(build_result=remote_result.build_result)
                store.save_build_result(remote_result.build_result)
            if remote_result.validation_result is not None:
                attach_content_hashes(validation_result=remote_result.validation_result)
                store.save_validation_result(remote_result.validation_result)
            bench_full = remote_result.benchmark_result
        else:
            if item.build is None:
                return
            bench_full = self._benchmark_with_retry(problem, candidate, item.build, BenchmarkStage.FULL)
        attach_content_hashes(benchmark_result=bench_full)
        store.save_benchmark_result(bench_full)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "benchmark") / "full.json",
            bench_full,
        )

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.QUEUED_BENCH_FULL,
            to_state=CandidateState.BENCH_FULL_DONE,
            reason=f"full benchmark status={bench_full.status.value}",
        )

        raw_score = (
            remote_result.raw_score
            if remote_result and remote_result.raw_score is not None
            else problem.score(bench_full, item.validation)
        )
        full_score = ScoreRecord(
            run_id=run_id,
            candidate_id=cid,
            stage=BenchmarkStage.FULL,
            scalar_fitness=(
                remote_result.scalar_fitness
                if remote_result and remote_result.scalar_fitness is not None
                else self._scalarize(raw_score)
            ),
            raw_score=raw_score,
        )
        attach_content_hashes(score_record=full_score)
        store.save_score(full_score)

        if remote_result:
            if remote_result.descriptor is None:
                artifacts.write_json(
                    artifacts.candidate_dir(run_id, cid, "remote") / "error_full.json",
                    {"error": "remote response missing descriptor"},
                )
                store.transition_state(
                    run_id=run_id,
                    candidate_id=cid,
                    from_state=CandidateState.BENCH_FULL_DONE,
                    to_state=CandidateState.DEAD_LETTER,
                    reason="remote response missing descriptor",
                )
                item.terminal_state = CandidateState.DEAD_LETTER
                return
            descriptor = remote_result.descriptor
        else:
            if item.build is None:
                return
            descriptor = problem.describe(candidate, item.build, bench_full)
        attach_content_hashes(descriptor=descriptor)
        store.save_descriptor(descriptor)
        artifacts.write_json(
            artifacts.candidate_dir(run_id, cid, "descriptor") / "descriptor.json",
            descriptor,
        )

        store.transition_state(
            run_id=run_id,
            candidate_id=cid,
            from_state=CandidateState.BENCH_FULL_DONE,
            to_state=CandidateState.SCORED,
            reason="full score saved",
        )

        item.full_benchmark = bench_full
        item.full_score = full_score
        item.descriptor = descriptor

    def _build_with_retry(self, problem: OptimizationProblem, candidate: Candidate) -> BuildExecution:
        retries = self.config.retry_policy.build_infra_retries
        attempts = 0
        while True:
            attempts += 1
            build = problem.build(candidate)
            if build.result.status not in {BuildStatus.INFRA_ERROR, BuildStatus.TIMEOUT}:
                return build
            if attempts > retries + 1:
                return build

    def _benchmark_with_retry(
        self,
        problem: OptimizationProblem,
        candidate: Candidate,
        build: BuildExecution,
        stage: BenchmarkStage,
    ) -> BenchmarkResult:
        retries = self.config.retry_policy.benchmark_infra_retries
        attempts = 0
        while True:
            attempts += 1
            result = problem.benchmark(candidate, build, stage)
            if result.status is not BenchmarkStatus.ERROR:
                return result
            if attempts > retries + 1:
                return result

    @staticmethod
    def _problem_config(problem: OptimizationProblem) -> dict[str, object]:
        cfg_method = getattr(problem, "to_config_dict", None)
        if callable(cfg_method):
            cfg = cfg_method()
            if isinstance(cfg, dict):
                return cfg
        return {}

    @staticmethod
    def _scalarize(score: float | dict[str, float]) -> float:
        if isinstance(score, float):
            return score
        if "fitness" in score:
            return float(score["fitness"])
        for value in score.values():
            return float(value)
        return float("-inf")

    @staticmethod
    def _error_benchmark_result(candidate: Candidate, stage: BenchmarkStage) -> BenchmarkResult:
        return BenchmarkResult(
            run_id=candidate.run_id,
            candidate_id=candidate.candidate_id,
            stage=stage,
            status=BenchmarkStatus.ERROR,
            samples=0,
            warmup_iters=0,
            timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
            env={"backend": "pipeline-error-placeholder"},
            profile={},
        )
