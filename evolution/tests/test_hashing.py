from __future__ import annotations

import unittest

from kernelswarm.hashing import attach_content_hashes
from kernelswarm.models import Candidate, CandidateOrigin, CandidateRepresentation, CompileConfig, LaunchConfig, SourceFile


class HashingTests(unittest.TestCase):
    def test_candidate_hash_is_stable_for_same_content(self) -> None:
        rep = CandidateRepresentation(
            language="cuda_cpp",
            entrypoints=["k"],
            files=[SourceFile(path="kernel.cu", content="extern \"C\" __global__ void k() {}")],
            params={"unroll": 2, "vec_width": 4},
            launch=LaunchConfig(),
            compile=CompileConfig(arch="sm_90", flags=["-O3"], defines={}),
        )
        c1 = Candidate.new(
            run_id="r1",
            parent_ids=[],
            origin=CandidateOrigin(island_id="a", agent_id="gen-1", operation="seed"),
            representation=rep,
            track="from_scratch",
            hypothesis="h",
        )
        c2 = Candidate.new(
            run_id="r1",
            parent_ids=[],
            origin=CandidateOrigin(island_id="a", agent_id="gen-9", operation="seed"),
            representation=rep,
            track="from_scratch",
            hypothesis="h",
        )
        attach_content_hashes(candidate=c1)
        attach_content_hashes(candidate=c2)
        self.assertEqual(c1.content_hash, c2.content_hash)


if __name__ == "__main__":
    unittest.main()
