"""Data models and WebSocket message schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Core models ──────────────────────────────────────────────────────────────

class TestCase(BaseModel):
    input: str  # JSON: {"args": [...]}
    expected_output: str  # JSON of expected result


class Problem(BaseModel):
    id: str
    title: str
    description: str
    function_signature: str
    test_cases: list[TestCase]
    hidden_test_cases: list[TestCase]
    timeout_seconds: float = 5.0


class Submission(BaseModel):
    agent_id: str
    code: str
    generation: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScoredSubmission(BaseModel):
    agent_id: str
    code: str
    generation: int
    execution_time_ms: float
    passed_all: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GenerationState(BaseModel):
    generation: int
    top_k: list[ScoredSubmission]
    all_submissions_count: int
    connected_agents: int
    phase: Literal["waiting", "generating", "evolving", "stopped"]
    best_time_ms: float | None = None


# ── Server → Agent messages ──────────────────────────────────────────────────

class GenerateMessage(BaseModel):
    type: Literal["generate"] = "generate"
    problem: Problem
    generation: int


class EvolveMessage(BaseModel):
    type: Literal["evolve"] = "evolve"
    problem: Problem
    generation: int
    top_solutions: list[ScoredSubmission]


class StopMessage(BaseModel):
    type: Literal["stop"] = "stop"
    best_solution: ScoredSubmission | None = None


# ── Agent → Server messages ──────────────────────────────────────────────────

class RegisterMessage(BaseModel):
    model_config = {"protected_namespaces": ()}

    type: Literal["register"] = "register"
    agent_id: str
    model_name: str


class SubmissionMessage(BaseModel):
    type: Literal["submission"] = "submission"
    agent_id: str
    code: str
    generation: int


# ── Server → Dashboard messages ──────────────────────────────────────────────

class StateUpdateMessage(BaseModel):
    type: Literal["state_update"] = "state_update"
    state: GenerationState
    history: list[dict[str, Any]]
