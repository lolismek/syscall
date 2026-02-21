from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import (
    BenchmarkResult,
    BenchmarkStage,
    BuildExecution,
    Candidate,
    Descriptor,
    StaticCheckResult,
    ValidationResult,
)


@dataclass(slots=True)
class ProblemRunContext:
    run_id: str
    seed: int


class OptimizationProblem(Protocol):
    def problem_id(self) -> str:
        ...

    def baseline(self, ctx: ProblemRunContext) -> Candidate | None:
        ...

    def seed_candidates(self, ctx: ProblemRunContext) -> list[Candidate]:
        ...

    def static_check(self, candidate: Candidate) -> StaticCheckResult:
        ...

    def build(self, candidate: Candidate) -> BuildExecution:
        ...

    def validate(self, candidate: Candidate, build: BuildExecution) -> ValidationResult:
        ...

    def benchmark(
        self,
        candidate: Candidate,
        build: BuildExecution,
        stage: BenchmarkStage,
    ) -> BenchmarkResult:
        ...

    def score(
        self,
        benchmark: BenchmarkResult,
        validation: ValidationResult,
    ) -> float | dict[str, float]:
        ...

    def describe(
        self,
        candidate: Candidate,
        build: BuildExecution,
        benchmark: BenchmarkResult,
    ) -> Descriptor:
        ...
