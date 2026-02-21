from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any

from .hashing import attach_content_hashes
from .map_elites import IslandPolicy
from .models import Candidate, CandidateOrigin, CandidateRepresentation, StaticCheckResult
from .nemotron import FAST_MODE, NemotronClient, NemotronResult, NemotronUsage


@dataclass(slots=True)
class GeneratorDecision:
    candidate: Candidate
    changed_knobs: dict[str, Any]
    expected_effect: str
    risk_level: str
    rejected: bool
    used_llm: bool
    usage: NemotronUsage | None = None


@dataclass(slots=True)
class JudgeDecision:
    compile_worthy: bool
    priority_score: float
    risk_tags: list[str]
    used_llm: bool
    usage: NemotronUsage | None = None


def _clone_representation(rep: CandidateRepresentation) -> CandidateRepresentation:
    return copy.deepcopy(rep)


def _normalize_block_x(value: int) -> int:
    value = max(32, min(1024, int(value)))
    return max(32, int(round(value / 32.0) * 32))


def _sanitize_param(name: str, value: Any) -> Any:
    if name == "unroll":
        return max(1, min(16, int(value)))
    if name == "vec_width":
        raw = int(value)
        options = [1, 2, 4, 8]
        return min(options, key=lambda item: abs(item - raw))
    if name == "block_size":
        return _normalize_block_x(int(value))
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


class GeneratorAgent:
    def __init__(
        self,
        *,
        agent_id: str,
        client: NemotronClient | None,
        rng: random.Random,
    ) -> None:
        self.agent_id = agent_id
        self.client = client
        self.rng = rng

    def propose(self, *, parent: Candidate, policy: IslandPolicy) -> GeneratorDecision:
        if self.client is None:
            return self._heuristic(parent=parent, policy=policy)

        try:
            response = self.client.chat_json(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(parent=parent, policy=policy),
                mode=FAST_MODE,
            )
            return self._from_llm(parent=parent, policy=policy, response=response)
        except Exception:
            return self._heuristic(parent=parent, policy=policy)

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are GeneratorAgent for CUDA kernel evolution. "
            "Return strict JSON only with this shape: "
            '{"reject": bool, "params_patch": object, "launch_patch": object, '
            '"changed_knobs": object, "expected_effect": string, "risk_level": string}. '
            "Do not include markdown."
        )

    def _user_prompt(self, *, parent: Candidate, policy: IslandPolicy) -> str:
        rep = parent.representation
        return (
            f"island_style={policy.style}\n"
            f"mutation_scale={policy.mutation_scale}\n"
            f"parent_params={rep.params}\n"
            f"parent_launch_block={rep.launch.block}\n"
            f"parent_compile={rep.compile}\n"
            "Prefer small valid mutations and keep output JSON strict."
        )

    def _from_llm(
        self,
        *,
        parent: Candidate,
        policy: IslandPolicy,
        response: NemotronResult,
    ) -> GeneratorDecision:
        payload = response.payload
        rejected = bool(payload.get("reject", False))
        params_patch = payload.get("params_patch", {})
        launch_patch = payload.get("launch_patch", {})
        changed_knobs = payload.get("changed_knobs", {})
        expected_effect = str(payload.get("expected_effect", "LLM-generated mutation"))
        risk_level = str(payload.get("risk_level", "medium"))

        mutated = self._apply_mutation(
            parent=parent,
            policy=policy,
            params_patch=params_patch if isinstance(params_patch, dict) else {},
            launch_patch=launch_patch if isinstance(launch_patch, dict) else {},
            expected_effect=expected_effect,
        )
        return GeneratorDecision(
            candidate=mutated,
            changed_knobs=changed_knobs if isinstance(changed_knobs, dict) else {},
            expected_effect=expected_effect,
            risk_level=risk_level,
            rejected=rejected,
            used_llm=True,
            usage=response.usage,
        )

    def _heuristic(self, *, parent: Candidate, policy: IslandPolicy) -> GeneratorDecision:
        rep = parent.representation
        params = dict(rep.params)
        changed: dict[str, Any] = {}

        if "unroll" in params:
            delta = self.rng.choice([-1, 1])
            if policy.style == "aggressive":
                delta = self.rng.choice([-2, -1, 1, 2])
            params["unroll"] = _sanitize_param("unroll", int(params["unroll"]) + delta)
            changed["unroll"] = params["unroll"]

        if "vec_width" in params:
            options = [1, 2, 4, 8]
            current = int(params["vec_width"])
            if policy.style == "memory_explorer":
                current = self.rng.choice(options)
            else:
                idx = options.index(_sanitize_param("vec_width", current))
                idx = max(0, min(len(options) - 1, idx + self.rng.choice([-1, 1])))
                current = options[idx]
            params["vec_width"] = _sanitize_param("vec_width", current)
            changed["vec_width"] = params["vec_width"]

        block_x = int(rep.launch.block[0]) if rep.launch.block else 256
        if policy.style == "occupancy_tuner":
            block_x = _normalize_block_x(block_x + self.rng.choice([-64, -32, 32, 64]))
        elif self.rng.random() < 0.30:
            block_x = _normalize_block_x(block_x + self.rng.choice([-32, 32]))
        changed["block_size"] = block_x

        mutated = self._apply_mutation(
            parent=parent,
            policy=policy,
            params_patch=params,
            launch_patch={"block_size": block_x},
            expected_effect=f"heuristic mutation for {policy.style}",
        )
        return GeneratorDecision(
            candidate=mutated,
            changed_knobs=changed,
            expected_effect=f"heuristic mutation for {policy.style}",
            risk_level="medium",
            rejected=False,
            used_llm=False,
            usage=None,
        )

    def _apply_mutation(
        self,
        *,
        parent: Candidate,
        policy: IslandPolicy,
        params_patch: dict[str, Any],
        launch_patch: dict[str, Any],
        expected_effect: str,
    ) -> Candidate:
        rep = _clone_representation(parent.representation)
        for key, value in params_patch.items():
            rep.params[key] = _sanitize_param(key, value)

        block_size = launch_patch.get("block_size")
        if block_size is not None:
            block_x = _normalize_block_x(int(block_size))
            rep.launch.block = (block_x, rep.launch.block[1], rep.launch.block[2])

        origin = CandidateOrigin(
            island_id=policy.island_id,
            agent_id=self.agent_id,
            operation="mutate",
        )
        candidate = Candidate.new(
            run_id=parent.run_id,
            parent_ids=[parent.candidate_id],
            origin=origin,
            representation=rep,
            track=parent.track,
            hypothesis=expected_effect,
        )
        attach_content_hashes(candidate=candidate)
        return candidate


class JudgeAgent:
    def __init__(
        self,
        *,
        agent_id: str,
        client: NemotronClient | None,
    ) -> None:
        self.agent_id = agent_id
        self.client = client

    def review(self, *, candidate: Candidate, static: StaticCheckResult) -> JudgeDecision:
        if not static.ok:
            return JudgeDecision(
                compile_worthy=False,
                priority_score=0.0,
                risk_tags=["static_check_failed"],
                used_llm=False,
                usage=None,
            )

        if self.client is None:
            return JudgeDecision(
                compile_worthy=True,
                priority_score=0.7,
                risk_tags=[],
                used_llm=False,
                usage=None,
            )

        try:
            response = self.client.chat_json(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(candidate),
                mode=FAST_MODE,
            )
            payload = response.payload
            return JudgeDecision(
                compile_worthy=bool(payload.get("compile_worthy", True)),
                priority_score=max(0.0, min(1.0, float(payload.get("priority_score", 0.7)))),
                risk_tags=[str(tag) for tag in payload.get("risk_tags", []) if isinstance(tag, (str, int, float))],
                used_llm=True,
                usage=response.usage,
            )
        except Exception:
            return JudgeDecision(
                compile_worthy=True,
                priority_score=0.7,
                risk_tags=[],
                used_llm=False,
                usage=None,
            )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are JudgeAgent for CUDA kernel evolution. "
            "Return strict JSON only: "
            '{"compile_worthy": bool, "priority_score": number, "risk_tags": array}.'
        )

    @staticmethod
    def _user_prompt(candidate: Candidate) -> str:
        rep = candidate.representation
        return (
            f"params={rep.params}\n"
            f"launch_block={rep.launch.block}\n"
            f"compile={rep.compile}\n"
            "Judge compile worthiness and risk tags."
        )


@dataclass(slots=True)
class SwarmUsage:
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0

    def add(self, usage: NemotronUsage | None) -> None:
        if usage is None:
            return
        self.llm_calls += 1
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.total_tokens += usage.total_tokens
        self.latency_ms += usage.latency_ms


@dataclass(slots=True)
class SwarmAgentPool:
    generators: list[GeneratorAgent]
    judges: list[JudgeAgent]
    usage: SwarmUsage = field(default_factory=SwarmUsage)
    _next_generator_idx: int = 0
    _next_judge_idx: int = 0

    @classmethod
    def create(
        cls,
        *,
        client: NemotronClient | None,
        rng: random.Random,
        generator_count: int = 32,
        judge_count: int = 32,
    ) -> "SwarmAgentPool":
        generators = [
            GeneratorAgent(agent_id=f"gen-{i:02d}", client=client, rng=rng)
            for i in range(generator_count)
        ]
        judges = [
            JudgeAgent(agent_id=f"judge-{i:02d}", client=client)
            for i in range(judge_count)
        ]
        return cls(generators=generators, judges=judges)

    def next_generator(self) -> GeneratorAgent:
        agent = self.generators[self._next_generator_idx % len(self.generators)]
        self._next_generator_idx += 1
        return agent

    def next_judge(self) -> JudgeAgent:
        agent = self.judges[self._next_judge_idx % len(self.judges)]
        self._next_judge_idx += 1
        return agent
