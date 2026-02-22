from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

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

    def generator_prompt_context(self) -> dict[str, Any]:
        """Return problem-specific context for the LLM generator agent.

        Keys may include: ref_source, problem_description, hardware,
        framework_instructions, optimization_hints, etc.
        Default implementations should return an empty dict.
        """
        ...
