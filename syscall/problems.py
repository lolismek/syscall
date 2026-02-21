"""Problem registry and loader."""

from __future__ import annotations

import importlib
from syscall.models import Problem

_REGISTRY: dict[str, str] = {
    "two_sum": "problems.two_sum",
}


def load_problem(name: str) -> Problem:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown problem: {name!r}. Available: {list(_REGISTRY)}")
    module = importlib.import_module(_REGISTRY[name])
    return module.problem
