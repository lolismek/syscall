from __future__ import annotations

import json
import unittest
from unittest import mock

from kernelswarm.models import BenchmarkStage
from kernelswarm.plugins.vector_add import VectorAddProblem
from kernelswarm.registry import default_problem_factories
from kernelswarm.remote import EvalWorkerService, RemoteEvaluatorClient
from kernelswarm.serialization import to_dict
from kernelswarm.sdk import ProblemRunContext


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class RemoteEvalContractTests(unittest.TestCase):
    def test_service_evaluate_returns_complete_payload(self) -> None:
        service = EvalWorkerService(default_problem_factories())
        problem = VectorAddProblem()
        ctx = ProblemRunContext(run_id="run-1", seed=42)
        candidate = problem.baseline(ctx)
        assert candidate is not None

        payload = {
            "schema_version": "v1",
            "request_id": "req-1",
            "run_id": candidate.run_id,
            "problem_id": problem.problem_id(),
            "stage": BenchmarkStage.QUICK.value,
            "candidate": to_dict(candidate),
        }
        response = service.evaluate(payload)

        self.assertTrue(response["ok"])
        self.assertIn("static_check", response)
        self.assertIsNotNone(response["build_result"])
        self.assertIsNotNone(response["validation_result"])
        self.assertIsNotNone(response["benchmark_result"])
        self.assertIsNotNone(response["descriptor"])
        self.assertIsNotNone(response["scalar_fitness"])

    def test_client_decodes_response(self) -> None:
        service = EvalWorkerService(default_problem_factories())
        problem = VectorAddProblem()
        ctx = ProblemRunContext(run_id="run-2", seed=7)
        candidate = problem.baseline(ctx)
        assert candidate is not None

        service_payload = {
            "schema_version": "v1",
            "request_id": "req-2",
            "run_id": candidate.run_id,
            "problem_id": problem.problem_id(),
            "stage": BenchmarkStage.QUICK.value,
            "candidate": to_dict(candidate),
        }
        service_response = service.evaluate(service_payload)

        with mock.patch("kernelswarm.remote.request.urlopen", return_value=_FakeHTTPResponse(service_response)):
            client = RemoteEvaluatorClient("http://eval-worker.invalid")
            result = client.evaluate(
                problem_id=problem.problem_id(),
                candidate=candidate,
                stage=BenchmarkStage.QUICK,
            )

        self.assertTrue(result.static_check.ok)
        self.assertIsNotNone(result.build_result)
        self.assertIsNotNone(result.validation_result)
        self.assertIsNotNone(result.benchmark_result)
        self.assertIsNotNone(result.descriptor)
        self.assertIsNotNone(result.scalar_fitness)

    def test_client_sends_problem_config(self) -> None:
        service = EvalWorkerService(default_problem_factories())
        problem = VectorAddProblem()
        ctx = ProblemRunContext(run_id="run-3", seed=9)
        candidate = problem.baseline(ctx)
        assert candidate is not None

        captured_payload: dict[str, object] = {}

        def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
            nonlocal captured_payload
            body = req.data.decode("utf-8")
            captured_payload = json.loads(body)
            service_response = service.evaluate(captured_payload)
            return _FakeHTTPResponse(service_response)

        with mock.patch("kernelswarm.remote.request.urlopen", side_effect=fake_urlopen):
            client = RemoteEvaluatorClient("http://eval-worker.invalid")
            client.evaluate(
                problem_id=problem.problem_id(),
                candidate=candidate,
                stage=BenchmarkStage.QUICK,
                problem_config={"backend": "python-sim", "quick_iters": 3},
            )

        self.assertEqual(captured_payload.get("problem_config"), {"backend": "python-sim", "quick_iters": 3})


if __name__ == "__main__":
    unittest.main()
