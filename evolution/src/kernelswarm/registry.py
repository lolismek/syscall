from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .plugins.kernelbench import KernelBenchProblem
from .plugins.reduction import ReductionProblem
from .plugins.stencil2d import Stencil2DProblem
from .plugins.vector_add import VectorAddProblem
from .sdk import OptimizationProblem

ProblemFactory = Callable[[dict[str, Any] | None], OptimizationProblem]


def default_problem_factories() -> dict[str, ProblemFactory]:
    return {
        "kernelbench_v1": KernelBenchProblem.from_config_dict,
        "reduction_v1": ReductionProblem.from_config_dict,
        "stencil2d_v1": Stencil2DProblem.from_config_dict,
        "vector_add_v1": VectorAddProblem.from_config_dict,
    }
