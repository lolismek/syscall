from __future__ import annotations

import copy
import json
import logging
import random
import re
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from .hashing import attach_content_hashes
from .map_elites import IslandPolicy
from .models import Candidate, CandidateOrigin, CandidateRepresentation, SourceFile, StaticCheckResult
from .nemotron import DEEP_MODE, FAST_MODE, NemotronClient, NemotronMode, NemotronResult, NemotronUsage

_SWARM_USAGE_LOCK = threading.Lock()
_MAX_SOURCE_MUTATIONS = 8
_MAX_MUTATION_FIELD_LEN = 4_000
_SOURCE_MUTATION_OPS = {"replace", "insert_before", "insert_after", "append", "prepend"}


@dataclass(slots=True)
class GeneratorDecision:
    candidate: Candidate
    changed_knobs: dict[str, Any]
    expected_effect: str
    risk_level: str
    rejected: bool
    used_llm: bool
    source_mutations: list[dict[str, str]] = field(default_factory=list)
    usage: NemotronUsage | None = None


@dataclass(slots=True)
class JudgeDecision:
    stage: str
    compile_worthy: bool
    priority_score: float
    risk_tags: list[str]
    used_llm: bool
    reasoning: str = ""
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


def _sanitize_text(value: Any, *, max_len: int = _MAX_MUTATION_FIELD_LEN) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text[:max_len]


def _normalize_source_mutation(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None

    op = _sanitize_text(raw.get("op"), max_len=32).strip().lower()
    if op not in _SOURCE_MUTATION_OPS:
        return None

    mutation: dict[str, str] = {"op": op}
    path = _sanitize_text(raw.get("path"), max_len=256).strip()
    if path:
        mutation["path"] = path

    target = _sanitize_text(raw.get("target"))
    replacement = _sanitize_text(raw.get("replacement"))
    content = _sanitize_text(raw.get("content"))

    if op in {"replace", "insert_before", "insert_after"} and not target:
        return None
    if op == "replace" and not replacement:
        return None
    if op in {"insert_before", "insert_after", "append", "prepend"} and not content:
        return None

    if target:
        mutation["target"] = target
    if replacement:
        mutation["replacement"] = replacement
    if content:
        mutation["content"] = content
    return mutation


def _select_source_file(files: list[SourceFile], path_hint: str | None) -> SourceFile | None:
    if not files:
        return None
    if path_hint:
        for src in files:
            if src.path == path_hint:
                return src
        basename = path_hint.rsplit("/", 1)[-1]
        for src in files:
            if src.path.rsplit("/", 1)[-1] == basename:
                return src
    return files[0]


def _apply_source_mutations(rep: CandidateRepresentation, raw_mutations: list[Any]) -> list[dict[str, str]]:
    applied: list[dict[str, str]] = []
    if not rep.files:
        return applied

    for raw in raw_mutations[:_MAX_SOURCE_MUTATIONS]:
        mutation = _normalize_source_mutation(raw)
        if mutation is None:
            continue
        source_file = _select_source_file(rep.files, mutation.get("path"))
        if source_file is None:
            continue

        content = source_file.content
        op = mutation["op"]
        if op == "replace":
            target = mutation.get("target", "")
            replacement = mutation.get("replacement", "")
            if target not in content:
                continue
            source_file.content = content.replace(target, replacement, 1)
            applied.append(mutation)
            continue

        if op == "insert_before":
            target = mutation.get("target", "")
            insert = mutation.get("content", "")
            idx = content.find(target)
            if idx < 0:
                continue
            source_file.content = content[:idx] + insert + content[idx:]
            applied.append(mutation)
            continue

        if op == "insert_after":
            target = mutation.get("target", "")
            insert = mutation.get("content", "")
            idx = content.find(target)
            if idx < 0:
                continue
            end = idx + len(target)
            source_file.content = content[:end] + insert + content[end:]
            applied.append(mutation)
            continue

        if op == "append":
            insert = mutation.get("content", "")
            source_file.content = content + insert
            applied.append(mutation)
            continue

        if op == "prepend":
            insert = mutation.get("content", "")
            source_file.content = insert + content
            applied.append(mutation)
            continue
    return applied


def _sync_source_macros_from_params(rep: CandidateRepresentation) -> None:
    unroll = rep.params.get("unroll")
    vec_width = rep.params.get("vec_width")
    for src in rep.files:
        text = src.content
        if unroll is not None:
            text = re.sub(r"(?m)^#define\s+UNROLL\s+\d+\s*$", f"#define UNROLL {int(unroll)}", text)
        if vec_width is not None:
            text = re.sub(r"(?m)^#define\s+VEC_WIDTH\s+\d+\s*$", f"#define VEC_WIDTH {int(vec_width)}", text)
        src.content = text


_KB_SYSTEM_PROMPT = """\
You are an expert GPU kernel engineer. Your task is to optimize a PyTorch model by writing \
a faster replacement implementation.

You will receive:
- The REFERENCE MODEL source code (the original PyTorch implementation to beat)
- The CURRENT CANDIDATE source code (your starting point to improve)
- The island optimization strategy and hardware details

You must return strict JSON (no markdown) with this shape:
{"full_source": string, "expected_effect": string, "risk_level": string}

The "full_source" field must contain a complete Python source file that defines `class ModelNew(nn.Module)`. \
This class must produce numerically identical outputs to the reference Model for the same inputs.

CRITICAL RULES:
- ModelNew must be a standalone nn.Module that accepts the same constructor arguments as Model
- ModelNew.forward must accept the same inputs and return the same outputs
- ModelNew MUST have the same learnable parameters (weight, bias, etc.) as the reference Model \
with the same shapes — dropping or ignoring learnable parameters is NOT allowed
- Write actual custom kernels: custom CUDA kernels via \
torch.utils.cpp_extension.load_inline, Triton kernels, or optimized PyTorch operations
- Do NOT use torch.compile — it is a black-box compiler, not a custom kernel optimization. \
The goal is to write explicit, hand-optimized GPU code.
- All imports must be at the top of the file
- Do not use try/except blocks

OPTIMIZATION STRATEGIES (pick what fits the island style):
- Operator fusion: combine multiple PyTorch ops into a single CUDA kernel
- Memory optimization: reduce global memory reads/writes, use shared memory for reuse
- Vectorized loads: use float4/int4 for coalesced memory access
- Loop unrolling and tiling for better instruction-level parallelism
- Warp-level primitives: __shfl_down_sync for reductions
- Minimize kernel launches: fuse sequential operations
- Use fast math intrinsics: __expf, rsqrtf, __fdividef
- Leverage tensor cores via TF32 or FP16 accumulation where precision allows

TRITON KERNEL CONSTRAINTS (follow these to avoid compilation errors):
- Triton tensor numel limit is 1048576 — you MUST tile larger tensors into blocks, \
do not load an entire tensor at once if its numel exceeds this limit
- BLOCK_SIZE must be a power of 2, use triton.next_power_of_2() but cap at 1024
- Use tl.constexpr for compile-time block size parameters in kernel signatures
- Always mask with tl.arange(0, BLOCK_SIZE) < n_elements for non-power-of-2 sizes
- Each Triton kernel program handles one tile/row via tl.program_id(0) — launch grid must cover all programs
- Weight and bias parameters must be loaded from pointers passed as arguments, never captured as closures
- When processing a tensor of shape (M, N), iterate over N in BLOCK_SIZE chunks inside the kernel, \
do NOT try to load all N elements at once if N could exceed 1048576

Do not include markdown fences. Return only the JSON object."""

_KB_USER_TEMPLATE = """\
=== REFERENCE MODEL (the PyTorch code you must beat) ===
{ref_source}

=== CURRENT CANDIDATE (your starting point — improve this, keep what works) ===
{candidate_source}

IMPORTANT: Write actual custom GPU kernels (Triton or CUDA). Do NOT use torch.compile — \
it is banned. Focus on fusing operations, reducing memory traffic, and writing efficient \
Triton or CUDA kernels that replace the PyTorch ops in the reference model.

=== CONTEXT ===
Hardware: {hardware}
Problem: {ref_name} (level {problem_level}, id {problem_id})
Backend: {backend}, Precision: {precision}
Island style: {island_style} (mutation_scale={mutation_scale})

{style_guidance}

Return the complete optimized Python source as "full_source" in the JSON response."""

_STYLE_GUIDANCE = {
    "aggressive": (
        "AGGRESSIVE island: Make bold architectural changes. Rewrite the entire forward pass with "
        "fused CUDA kernels. Maximize throughput even at the risk of compilation failures. "
        "Try advanced techniques: custom CUDA kernels with shared memory tiling, warp-level "
        "reductions, vectorized loads, operator fusion."
    ),
    "memory_explorer": (
        "MEMORY EXPLORER island: Focus on memory access patterns. Reduce global memory traffic "
        "through shared memory caching, register blocking, and coalesced access. Try different "
        "tiling strategies and data layouts."
    ),
    "occupancy_tuner": (
        "OCCUPANCY TUNER island: Focus on GPU utilization. Tune block sizes, grid dimensions, "
        "and register usage to maximize occupancy. Consider using fewer registers per thread "
        "to allow more concurrent warps."
    ),
    "correctness_first": (
        "CORRECTNESS FIRST island: Make careful, incremental optimizations. Start with simple "
        "fusions (e.g., fuse elementwise ops) that are easy to verify. Prefer torch.compile "
        "or simple Triton kernels over complex hand-written CUDA."
    ),
}


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
        self._lock = threading.Lock()

    def propose(
        self,
        *,
        parent: Candidate,
        policy: IslandPolicy,
        prompt_context: dict[str, Any] | None = None,
    ) -> GeneratorDecision:
        with self._lock:
            is_kb = prompt_context is not None and prompt_context.get("mode") == "kernelbench"
            if self.client is None:
                logger.warning("[%s] No LLM client configured, using heuristic fallback", self.agent_id)
                if is_kb:
                    return self._heuristic_kb(parent=parent, policy=policy)
                return self._heuristic(parent=parent, policy=policy)

            try:
                if is_kb:
                    return self._propose_kb(parent=parent, policy=policy, ctx=prompt_context)
                response = self.client.chat_json(
                    system_prompt=self._system_prompt(),
                    user_prompt=self._user_prompt(parent=parent, policy=policy),
                    mode=FAST_MODE,
                )
                return self._from_llm(parent=parent, policy=policy, response=response)
            except Exception:
                logger.error(
                    "[%s] LLM proposal failed, falling back to heuristic",
                    self.agent_id,
                    exc_info=True,
                )
                if is_kb:
                    return self._heuristic_kb(parent=parent, policy=policy)
                return self._heuristic(parent=parent, policy=policy)

    def _propose_kb(
        self,
        *,
        parent: Candidate,
        policy: IslandPolicy,
        ctx: dict[str, Any],
    ) -> GeneratorDecision:
        """KernelBench-specific proposal using rich problem context."""
        candidate_source = ""
        for src in parent.representation.files[:1]:
            candidate_source = src.content

        style_guidance = _STYLE_GUIDANCE.get(policy.style, _STYLE_GUIDANCE["aggressive"])
        # Add a random seed to encourage diverse outputs for the same parent.
        diversity_hint = f"\nDiversity seed: {self.rng.randint(0, 999999):06d}. Try a DIFFERENT optimization approach than previous attempts.\n"
        user_prompt = _KB_USER_TEMPLATE.format(
            ref_source=ctx.get("ref_source", "# reference source unavailable"),
            candidate_source=candidate_source or "# empty",
            hardware=ctx.get("hardware", "NVIDIA GPU"),
            ref_name=ctx.get("ref_name", "unknown"),
            problem_level=ctx.get("problem_level", "?"),
            problem_id=ctx.get("problem_id", "?"),
            backend=ctx.get("backend", "cuda"),
            precision=ctx.get("precision", "fp32"),
            island_style=policy.style,
            mutation_scale=policy.mutation_scale,
            style_guidance=style_guidance,
        ) + diversity_hint

        # Append failure feedback so the LLM avoids repeating the same mistakes.
        recent_failures = ctx.get("recent_failures", [])
        if recent_failures:
            failure_lines = "\n".join(f"- {str(f)[:200]}" for f in recent_failures[:3])
            user_prompt += (
                "\n=== RECENT FAILURES (from mutations of this parent — avoid these mistakes) ===\n"
                + failure_lines
                + "\n"
            )

        # KernelBench needs enough tokens for full kernel source.
        # High temperature for diversity — dedup filter catches repeats.
        kb_mode = NemotronMode(name="kernelbench", temperature=0.9, max_tokens=4096, enable_thinking=False)
        response = self.client.chat_json(  # type: ignore[union-attr]
            system_prompt=_KB_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            mode=kb_mode,
        )

        payload = response.payload
        full_source = str(payload.get("full_source", "")).strip()
        expected_effect = str(payload.get("expected_effect", "LLM kernel rewrite"))
        risk_level = str(payload.get("risk_level", "high"))

        if not full_source or "ModelNew" not in full_source:
            logger.warning(
                "[%s] LLM response missing ModelNew class (got %d chars, preview: %.200s)",
                self.agent_id,
                len(full_source),
                full_source[:200] if full_source else "<empty>",
            )
            return self._heuristic_kb(parent=parent, policy=policy)

        rep = _clone_representation(parent.representation)
        if rep.files:
            rep.files[0].content = full_source
        else:
            rep.files = [SourceFile(path="model_new.py", content=full_source)]
        rep.patch = json.dumps({"full_rewrite": True}, sort_keys=True)

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
        return GeneratorDecision(
            candidate=candidate,
            changed_knobs={"full_rewrite": True},
            expected_effect=expected_effect,
            risk_level=risk_level,
            rejected=False,
            used_llm=True,
            source_mutations=[{"op": "full_rewrite"}],
            usage=response.usage,
        )

    def _heuristic_kb(self, *, parent: Candidate, policy: IslandPolicy) -> GeneratorDecision:
        """Heuristic fallback for KernelBench: reject immediately.

        Without LLM, we can't write new kernels. Returning the parent unchanged
        wastes an eval slot and always hits the dedup filter.
        """
        rep = _clone_representation(parent.representation)
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
            hypothesis="heuristic rejected (LLM required for kernelbench)",
        )
        attach_content_hashes(candidate=candidate)
        return GeneratorDecision(
            candidate=candidate,
            changed_knobs={},
            expected_effect="heuristic rejected (LLM required for kernelbench)",
            risk_level="low",
            rejected=True,
            used_llm=False,
            source_mutations=[],
            usage=None,
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are GeneratorAgent for CUDA kernel evolution. "
            "Return strict JSON only with this shape: "
            '{"reject": bool, "params_patch": object, "launch_patch": object, "source_mutations": array, '
            '"changed_knobs": object, "expected_effect": string, "risk_level": string}. '
            "source_mutations supports ops: replace, insert_before, insert_after, append, prepend. "
            "Each op object may include path/target/replacement/content. "
            "Do not include markdown."
        )

    def _user_prompt(self, *, parent: Candidate, policy: IslandPolicy) -> str:
        rep = parent.representation
        source_preview = ""
        for src in rep.files[:2]:
            snippet = src.content[:1_200].replace("\r", "")
            source_preview += f"\n--- file:{src.path} ---\n{snippet}\n"
        return (
            f"island_style={policy.style}\n"
            f"mutation_scale={policy.mutation_scale}\n"
            f"parent_params={rep.params}\n"
            f"parent_launch_block={rep.launch.block}\n"
            f"parent_compile={rep.compile}\n"
            f"parent_language={rep.language}\n"
            f"parent_patch={rep.patch}\n"
            f"parent_source_preview={source_preview}\n"
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
        raw_source_mutations = payload.get("source_mutations", [])
        changed_knobs = payload.get("changed_knobs", {})
        expected_effect = str(payload.get("expected_effect", "LLM-generated mutation"))
        risk_level = str(payload.get("risk_level", "medium"))
        source_mutations = (
            raw_source_mutations
            if isinstance(raw_source_mutations, list)
            else []
        )

        mutated, applied_source_mutations = self._apply_mutation(
            parent=parent,
            policy=policy,
            params_patch=params_patch if isinstance(params_patch, dict) else {},
            launch_patch=launch_patch if isinstance(launch_patch, dict) else {},
            source_mutations=source_mutations,
            expected_effect=expected_effect,
        )
        return GeneratorDecision(
            candidate=mutated,
            changed_knobs=changed_knobs if isinstance(changed_knobs, dict) else {},
            expected_effect=expected_effect,
            risk_level=risk_level,
            rejected=rejected,
            used_llm=True,
            source_mutations=applied_source_mutations,
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

        source_mutations: list[dict[str, str]] = []
        if policy.style == "aggressive" and self.rng.random() < 0.35:
            source_mutations.append(
                {
                    "op": "insert_before",
                    "target": "for (int idx_base = base; idx_base < n; idx_base += stride) {",
                    "content": "// heuristic_hint: aggressive unroll exploration\n",
                }
            )

        mutated, applied_source_mutations = self._apply_mutation(
            parent=parent,
            policy=policy,
            params_patch=params,
            launch_patch={"block_size": block_x},
            source_mutations=source_mutations,
            expected_effect=f"heuristic mutation for {policy.style}",
        )
        return GeneratorDecision(
            candidate=mutated,
            changed_knobs=changed,
            expected_effect=f"heuristic mutation for {policy.style}",
            risk_level="medium",
            rejected=False,
            used_llm=False,
            source_mutations=applied_source_mutations,
            usage=None,
        )

    def _apply_mutation(
        self,
        *,
        parent: Candidate,
        policy: IslandPolicy,
        params_patch: dict[str, Any],
        launch_patch: dict[str, Any],
        source_mutations: list[Any],
        expected_effect: str,
    ) -> tuple[Candidate, list[dict[str, str]]]:
        rep = _clone_representation(parent.representation)
        applied_params_patch: dict[str, Any] = {}
        for key, value in params_patch.items():
            sanitized = _sanitize_param(key, value)
            rep.params[key] = sanitized
            applied_params_patch[key] = sanitized

        block_size = launch_patch.get("block_size")
        applied_launch_patch: dict[str, Any] = {}
        if block_size is not None:
            block_x = _normalize_block_x(int(block_size))
            rep.launch.block = (block_x, rep.launch.block[1], rep.launch.block[2])
            applied_launch_patch["block_size"] = block_x

        _sync_source_macros_from_params(rep)
        applied_source_mutations = _apply_source_mutations(rep, source_mutations)
        patch_payload: dict[str, Any] = {}
        if applied_params_patch:
            patch_payload["params_patch"] = applied_params_patch
        if applied_launch_patch:
            patch_payload["launch_patch"] = applied_launch_patch
        if applied_source_mutations:
            patch_payload["source_mutations"] = applied_source_mutations
        rep.patch = json.dumps(patch_payload, sort_keys=True) if patch_payload else None

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
        return candidate, applied_source_mutations


class JudgeAgent:
    def __init__(
        self,
        *,
        agent_id: str,
        client: NemotronClient | None,
    ) -> None:
        self.agent_id = agent_id
        self.client = client

    def review(
        self,
        *,
        candidate: Candidate,
        static: StaticCheckResult,
        stage: str = "triage",
        quick_fitness: float | None = None,
        quick_median_us: float | None = None,
        island_top_fitness: float | None = None,
        prompt_context: dict[str, Any] | None = None,
    ) -> JudgeDecision:
        stage = (stage or "triage").strip().lower()

        if stage == "triage" and not static.ok:
            return JudgeDecision(
                stage=stage,
                compile_worthy=False,
                priority_score=0.0,
                risk_tags=["static_check_failed"],
                used_llm=False,
                reasoning="static check failed",
                usage=None,
            )

        if stage == "full_gate" and quick_fitness is not None and quick_fitness <= -1e17:
            return JudgeDecision(
                stage=stage,
                compile_worthy=False,
                priority_score=0.0,
                risk_tags=["quick_invalid"],
                used_llm=False,
                reasoning="quick stage indicates invalid candidate",
                usage=None,
            )

        # For KernelBench: skip LLM judge entirely — static check is sufficient.
        # The LLM judge was trained on CUDA kernels and incorrectly rejects valid
        # Python-based KernelBench candidates.
        is_kb = prompt_context is not None and prompt_context.get("mode") == "kernelbench"
        if is_kb:
            heuristic_priority = 0.7
            if stage == "full_gate" and quick_fitness is not None:
                heuristic_priority = max(0.1, min(1.0, quick_fitness / 200.0))
            return JudgeDecision(
                stage=stage,
                compile_worthy=True,
                priority_score=heuristic_priority,
                risk_tags=[],
                used_llm=False,
                reasoning="kernelbench auto-allow (static check sufficient)",
                usage=None,
            )

        if self.client is None:
            heuristic_priority = 0.7
            if stage == "full_gate" and quick_fitness is not None:
                heuristic_priority = max(0.1, min(1.0, quick_fitness / 200.0))
            return JudgeDecision(
                stage=stage,
                compile_worthy=True,
                priority_score=heuristic_priority,
                risk_tags=[],
                used_llm=False,
                reasoning="heuristic allow",
                usage=None,
            )

        try:
            response = self.client.chat_json(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(
                    candidate=candidate,
                    stage=stage,
                    quick_fitness=quick_fitness,
                    quick_median_us=quick_median_us,
                    island_top_fitness=island_top_fitness,
                    static=static,
                ),
                mode=FAST_MODE,
            )
            payload = response.payload
            return JudgeDecision(
                stage=stage,
                compile_worthy=bool(payload.get("compile_worthy", True)),
                priority_score=max(0.0, min(1.0, float(payload.get("priority_score", 0.7)))),
                risk_tags=[str(tag) for tag in payload.get("risk_tags", []) if isinstance(tag, (str, int, float))],
                reasoning=_sanitize_text(payload.get("reasoning"), max_len=600),
                used_llm=True,
                usage=response.usage,
            )
        except Exception:
            return JudgeDecision(
                stage=stage,
                compile_worthy=True,
                priority_score=0.7,
                risk_tags=[],
                used_llm=False,
                reasoning="judge fallback allow",
                usage=None,
            )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are JudgeAgent for CUDA kernel evolution. "
            "Return strict JSON only: "
            '{"compile_worthy": bool, "priority_score": number, "risk_tags": array, "reasoning": string}.'
        )

    @staticmethod
    def _user_prompt(
        *,
        candidate: Candidate,
        stage: str,
        quick_fitness: float | None,
        quick_median_us: float | None,
        island_top_fitness: float | None,
        static: StaticCheckResult,
    ) -> str:
        rep = candidate.representation
        source_preview = rep.files[0].content[:1_200] if rep.files else ""
        return (
            f"stage={stage}\n"
            f"candidate_id={candidate.candidate_id}\n"
            f"params={rep.params}\n"
            f"launch_block={rep.launch.block}\n"
            f"compile={rep.compile}\n"
            f"static_ok={static.ok}\n"
            f"static_reasons={static.reasons}\n"
            f"quick_fitness={quick_fitness}\n"
            f"quick_median_us={quick_median_us}\n"
            f"island_top_fitness={island_top_fitness}\n"
            f"source_preview={source_preview}\n"
            "For stage=triage, decide compile worthiness. "
            "For stage=full_gate, decide if full benchmark should run."
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
        with _SWARM_USAGE_LOCK:
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
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

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
            GeneratorAgent(
                agent_id=f"gen-{i:02d}",
                client=client,
                rng=random.Random(rng.randrange(0, 2**63 - 1)),
            )
            for i in range(generator_count)
        ]
        judges = [
            JudgeAgent(agent_id=f"judge-{i:02d}", client=client)
            for i in range(judge_count)
        ]
        return cls(generators=generators, judges=judges)

    def next_generator(self) -> GeneratorAgent:
        with self._lock:
            agent = self.generators[self._next_generator_idx % len(self.generators)]
            self._next_generator_idx += 1
            return agent

    def next_judge(self) -> JudgeAgent:
        with self._lock:
            agent = self.judges[self._next_judge_idx % len(self.judges)]
            self._next_judge_idx += 1
            return agent
