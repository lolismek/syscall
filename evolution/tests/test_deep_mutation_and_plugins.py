from __future__ import annotations

import random
import unittest

from kernelswarm.agents import GeneratorAgent, JudgeAgent
from kernelswarm.hashing import attach_content_hashes
from kernelswarm.map_elites import IslandPolicy
from kernelswarm.models import (
    BenchmarkStage,
    Candidate,
    CandidateOrigin,
    CandidateRepresentation,
    CompileConfig,
    LaunchConfig,
    SourceFile,
    StaticCheckResult,
)
from kernelswarm.plugins.reduction import ReductionConfig, ReductionProblem
from kernelswarm.plugins.stencil2d import Stencil2DConfig, Stencil2DProblem
from kernelswarm.sdk import ProblemRunContext


class DeepMutationTests(unittest.TestCase):
    def test_apply_mutation_updates_source_and_patch_payload(self) -> None:
        rep = CandidateRepresentation(
            language="cuda_cpp",
            entrypoints=["kernel"],
            files=[
                SourceFile(
                    path="kernel.cu",
                    content=(
                        "#define UNROLL 1\n"
                        "#define VEC_WIDTH 1\n"
                        "__global__ void kernel() {\n"
                        "  int x = 0;\n"
                        "}\n"
                    ),
                )
            ],
            params={"unroll": 1, "vec_width": 1},
            launch=LaunchConfig(block=(256, 1, 1)),
            compile=CompileConfig(arch="sm_90", flags=["-O3"], defines={}),
        )
        parent = Candidate.new(
            run_id="run-test",
            parent_ids=[],
            origin=CandidateOrigin(island_id="island-a", agent_id="seed", operation="seed"),
            representation=rep,
            track="from_scratch",
            hypothesis="seed",
        )
        attach_content_hashes(candidate=parent)

        agent = GeneratorAgent(agent_id="gen-test", client=None, rng=random.Random(7))
        policy = IslandPolicy(island_id="island-b", style="aggressive", mutation_scale=1.0)
        mutated, applied = agent._apply_mutation(  # noqa: SLF001 - intentional whitebox test
            parent=parent,
            policy=policy,
            params_patch={"unroll": 4, "vec_width": 2},
            launch_patch={"block_size": 320},
            source_mutations=[
                {"op": "replace", "target": "int x = 0;", "replacement": "int x = 1;"},
                {"op": "append", "content": "\n// extra mutation\n"},
            ],
            expected_effect="test deep mutation",
        )

        self.assertEqual(mutated.representation.params["unroll"], 4)
        self.assertEqual(mutated.representation.params["vec_width"], 2)
        self.assertEqual(mutated.representation.launch.block[0], 320)
        self.assertEqual(len(applied), 2)
        src = mutated.representation.files[0].content
        self.assertIn("#define UNROLL 4", src)
        self.assertIn("#define VEC_WIDTH 2", src)
        self.assertIn("int x = 1;", src)
        self.assertIn("// extra mutation", src)
        self.assertIsNotNone(mutated.representation.patch)
        self.assertIn("source_mutations", str(mutated.representation.patch))

    def test_judge_has_stage_specific_behavior(self) -> None:
        judge = JudgeAgent(agent_id="judge-test", client=None)
        static_ok = StaticCheckResult(candidate_id="c1", ok=True, reasons=[])
        dummy = Candidate.new(
            run_id="run-test",
            parent_ids=[],
            origin=CandidateOrigin(island_id="island-a", agent_id="seed", operation="seed"),
            representation=CandidateRepresentation(
                language="cuda_cpp",
                entrypoints=["k"],
                files=[SourceFile(path="k.cu", content="__global__ void k(){}")],
            ),
            track="from_scratch",
            hypothesis="seed",
        )

        rejected_full = judge.review(
            candidate=dummy,
            static=static_ok,
            stage="full_gate",
            quick_fitness=-1e18,
            quick_median_us=None,
            island_top_fitness=None,
        )
        self.assertFalse(rejected_full.compile_worthy)
        self.assertEqual(rejected_full.stage, "full_gate")

        accepted_full = judge.review(
            candidate=dummy,
            static=static_ok,
            stage="full_gate",
            quick_fitness=140.0,
            quick_median_us=9000.0,
            island_top_fitness=150.0,
        )
        self.assertTrue(accepted_full.compile_worthy)
        self.assertEqual(accepted_full.stage, "full_gate")


class MultiProblemPluginTests(unittest.TestCase):
    def test_reduction_plugin_baseline_flow(self) -> None:
        problem = ReductionProblem(
            ReductionConfig(
                backend="python-sim",
                quick_size=2048,
                full_size=4096,
                quick_iters=3,
                full_iters=4,
                seed_count=2,
            )
        )
        ctx = ProblemRunContext(run_id="run-reduction", seed=1)
        candidate = problem.baseline(ctx)
        assert candidate is not None
        static = problem.static_check(candidate)
        self.assertTrue(static.ok)
        build = problem.build(candidate)
        validation = problem.validate(candidate, build)
        self.assertEqual(validation.status.value, "pass")
        quick = problem.benchmark(candidate, build, BenchmarkStage.QUICK)
        self.assertEqual(quick.status.value, "success")
        score = problem.score(quick, validation)
        self.assertIn("fitness", score)
        desc = problem.describe(candidate, build, quick)
        self.assertEqual(desc.descriptor_name, "reduction_v1")

    def test_stencil_plugin_baseline_flow(self) -> None:
        problem = Stencil2DProblem(
            Stencil2DConfig(
                backend="python-sim",
                quick_size=32,
                full_size=48,
                quick_iters=2,
                full_iters=3,
                validation_size=32,
                seed_count=2,
            )
        )
        ctx = ProblemRunContext(run_id="run-stencil", seed=2)
        candidate = problem.baseline(ctx)
        assert candidate is not None
        static = problem.static_check(candidate)
        self.assertTrue(static.ok)
        build = problem.build(candidate)
        validation = problem.validate(candidate, build)
        self.assertEqual(validation.status.value, "pass")
        quick = problem.benchmark(candidate, build, BenchmarkStage.QUICK)
        self.assertEqual(quick.status.value, "success")
        score = problem.score(quick, validation)
        self.assertIn("fitness", score)
        desc = problem.describe(candidate, build, quick)
        self.assertEqual(desc.descriptor_name, "stencil2d_v1")


if __name__ == "__main__":
    unittest.main()
