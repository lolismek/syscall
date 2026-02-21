from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .plugins.vector_add import VectorAddProblem
from .sdk import OptimizationProblem

ProblemFactory = Callable[[dict[str, Any] | None], OptimizationProblem]


def default_problem_factories() -> dict[str, ProblemFactory]:
    return {
        "vector_add_v1": VectorAddProblem.from_config_dict,
    }
