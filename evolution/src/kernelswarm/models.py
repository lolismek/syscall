from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class CandidateState(str, Enum):
    PROPOSED = "PROPOSED"
    TRIAGED = "TRIAGED"
    REJECTED_STATIC = "REJECTED_STATIC"
    QUEUED_BUILD = "QUEUED_BUILD"
    BUILDING = "BUILDING"
    BUILD_FAILED = "BUILD_FAILED"
    QUEUED_VALIDATE = "QUEUED_VALIDATE"
    VALIDATING = "VALIDATING"
    INVALID = "INVALID"
    QUEUED_BENCH_QUICK = "QUEUED_BENCH_QUICK"
    BENCH_QUICK_DONE = "BENCH_QUICK_DONE"
    QUEUED_BENCH_FULL = "QUEUED_BENCH_FULL"
    BENCH_FULL_DONE = "BENCH_FULL_DONE"
    SCORED = "SCORED"
    ARCHIVED = "ARCHIVED"
    DEAD_LETTER = "DEAD_LETTER"


class BuildStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    INFRA_ERROR = "infra_error"


class ValidationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    TIMEOUT = "timeout"


class BenchmarkStage(str, Enum):
    QUICK = "quick"
    FULL = "full"


class BenchmarkStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(slots=True)
class SourceFile:
    path: str
    content: str


@dataclass(slots=True)
class LaunchConfig:
    grid: tuple[int | str, int, int] = ("auto", 1, 1)
    block: tuple[int, int, int] = (256, 1, 1)
    dynamic_smem_bytes: int = 0
    stream_mode: str = "default"


@dataclass(slots=True)
class CompileConfig:
    arch: str = "sm_90"
    flags: list[str] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateRepresentation:
    language: str
    entrypoints: list[str]
    files: list[SourceFile]
    patch: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    launch: LaunchConfig = field(default_factory=LaunchConfig)
    compile: CompileConfig = field(default_factory=CompileConfig)


@dataclass(slots=True)
class CandidateOrigin:
    island_id: str
    agent_id: str
    operation: str


@dataclass(slots=True)
class Candidate:
    run_id: str
    candidate_id: str
    parent_ids: list[str]
    origin: CandidateOrigin
    representation: CandidateRepresentation
    track: str
    hypothesis: str
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""

    @staticmethod
    def new(
        run_id: str,
        parent_ids: list[str],
        origin: CandidateOrigin,
        representation: CandidateRepresentation,
        track: str,
        hypothesis: str,
    ) -> "Candidate":
        return Candidate(
            run_id=run_id,
            candidate_id=str(uuid4()),
            parent_ids=parent_ids,
            origin=origin,
            representation=representation,
            track=track,
            hypothesis=hypothesis,
        )


@dataclass(slots=True)
class StaticCheckResult:
    candidate_id: str
    ok: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BuildResult:
    run_id: str
    candidate_id: str
    status: BuildStatus
    build_backend: str
    duration_ms: int
    stderr_digest: str
    artifacts: dict[str, str] = field(default_factory=dict)
    compiler_metrics: dict[str, int | float] = field(default_factory=dict)
    toolchain_fingerprint: dict[str, str] = field(default_factory=dict)
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""


@dataclass(slots=True)
class BuildExecution:
    result: BuildResult
    runtime: Any | None = None


@dataclass(slots=True)
class ValidationTolerance:
    mode: str = "rtol_atol"
    rtol: float = 1e-6
    atol: float = 1e-6


@dataclass(slots=True)
class ValidationFailureCase:
    case_id: str
    summary: str


@dataclass(slots=True)
class ValidationResult:
    run_id: str
    candidate_id: str
    status: ValidationStatus
    tests_total: int
    tests_passed: int
    tolerance: ValidationTolerance
    max_abs_error: float = 0.0
    max_rel_error: float = 0.0
    failing_cases: list[ValidationFailureCase] = field(default_factory=list)
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""


@dataclass(slots=True)
class BenchmarkTiming:
    median_us: float
    p95_us: float
    mean_us: float
    stdev_us: float
    cov: float


@dataclass(slots=True)
class BenchmarkResult:
    run_id: str
    candidate_id: str
    stage: BenchmarkStage
    status: BenchmarkStatus
    samples: int
    warmup_iters: int
    timing: BenchmarkTiming
    env: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""


@dataclass(slots=True)
class Descriptor:
    run_id: str
    candidate_id: str
    descriptor_name: str
    values: dict[str, int | float]
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""


@dataclass(slots=True)
class ScoreRecord:
    run_id: str
    candidate_id: str
    stage: BenchmarkStage
    scalar_fitness: float
    raw_score: float | dict[str, float]
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)
    content_hash: str = ""


@dataclass(slots=True)
class RunManifest:
    run_id: str
    problem_id: str
    seed: int
    python_version: str
    platform: str
    toolchain: dict[str, str]
    git_commit: str | None
    schema_version: str = "v1"
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class RunSummary:
    run_id: str
    problem_id: str
    total_candidates: int
    quick_scored: int
    full_scored: int
    best_candidate_id: str | None
    best_fitness: float | None
    report_path: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class QueueMessage:
    msg_id: str
    run_id: str
    candidate_id: str
    task_type: str
    attempt: int
    created_at: datetime
    not_before: datetime | None
    priority: int
    idempotency_key: str
    payload: dict[str, Any]


@dataclass(slots=True)
class RetryPolicy:
    build_infra_retries: int = 1
    build_timeout_retries: int = 0
    benchmark_infra_retries: int = 1
