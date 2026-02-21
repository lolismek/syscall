from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib import request
from uuid import uuid4

from .hashing import attach_content_hashes
from .models import (
    BenchmarkResult,
    BenchmarkStage,
    BenchmarkStatus,
    BenchmarkTiming,
    BuildExecution,
    BuildResult,
    BuildStatus,
    Candidate,
    CandidateOrigin,
    CandidateRepresentation,
    CompileConfig,
    Descriptor,
    LaunchConfig,
    SourceFile,
    StaticCheckResult,
    ValidationFailureCase,
    ValidationResult,
    ValidationStatus,
    ValidationTolerance,
)
from .sdk import OptimizationProblem
from .serialization import to_dict


class RemoteEvaluationError(RuntimeError):
    pass


@dataclass(slots=True)
class RemoteEvaluationResult:
    static_check: StaticCheckResult
    build_result: BuildResult | None
    validation_result: ValidationResult | None
    benchmark_result: BenchmarkResult | None
    descriptor: Descriptor | None
    raw_score: float | dict[str, float] | None
    scalar_fitness: float | None


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now().astimezone()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def candidate_from_dict(data: dict[str, Any]) -> Candidate:
    files = [SourceFile(path=item["path"], content=item["content"]) for item in data["representation"]["files"]]
    launch_data = data["representation"].get("launch", {})
    compile_data = data["representation"].get("compile", {})

    rep = CandidateRepresentation(
        language=data["representation"]["language"],
        entrypoints=list(data["representation"]["entrypoints"]),
        files=files,
        patch=data["representation"].get("patch"),
        params=dict(data["representation"].get("params", {})),
        launch=LaunchConfig(
            grid=tuple(launch_data.get("grid", ("auto", 1, 1))),
            block=tuple(launch_data.get("block", (256, 1, 1))),
            dynamic_smem_bytes=int(launch_data.get("dynamic_smem_bytes", 0)),
            stream_mode=str(launch_data.get("stream_mode", "default")),
        ),
        compile=CompileConfig(
            arch=str(compile_data.get("arch", "sm_90")),
            flags=list(compile_data.get("flags", [])),
            defines=dict(compile_data.get("defines", {})),
        ),
    )

    candidate = Candidate(
        run_id=str(data["run_id"]),
        candidate_id=str(data["candidate_id"]),
        parent_ids=[str(item) for item in data.get("parent_ids", [])],
        origin=CandidateOrigin(
            island_id=str(data["origin"]["island_id"]),
            agent_id=str(data["origin"]["agent_id"]),
            operation=str(data["origin"]["operation"]),
        ),
        representation=rep,
        track=str(data.get("track", "from_scratch")),
        hypothesis=str(data.get("hypothesis", "")),
        schema_version=str(data.get("schema_version", "v1")),
        created_at=_parse_datetime(data.get("created_at")),
        content_hash=str(data.get("content_hash", "")),
    )
    return candidate


def static_check_from_dict(data: dict[str, Any]) -> StaticCheckResult:
    return StaticCheckResult(
        candidate_id=str(data["candidate_id"]),
        ok=bool(data["ok"]),
        reasons=[str(item) for item in data.get("reasons", [])],
    )


def build_result_from_dict(data: dict[str, Any]) -> BuildResult:
    return BuildResult(
        run_id=str(data["run_id"]),
        candidate_id=str(data["candidate_id"]),
        status=BuildStatus(str(data["status"])),
        build_backend=str(data["build_backend"]),
        duration_ms=int(data["duration_ms"]),
        stderr_digest=str(data["stderr_digest"]),
        artifacts=dict(data.get("artifacts", {})),
        compiler_metrics=dict(data.get("compiler_metrics", {})),
        toolchain_fingerprint=dict(data.get("toolchain_fingerprint", {})),
        schema_version=str(data.get("schema_version", "v1")),
        created_at=_parse_datetime(data.get("created_at")),
        content_hash=str(data.get("content_hash", "")),
    )


def validation_result_from_dict(data: dict[str, Any]) -> ValidationResult:
    tol_data = data.get("tolerance", {})
    failures = [
        ValidationFailureCase(case_id=str(item["case_id"]), summary=str(item["summary"]))
        for item in data.get("failing_cases", [])
    ]
    return ValidationResult(
        run_id=str(data["run_id"]),
        candidate_id=str(data["candidate_id"]),
        status=ValidationStatus(str(data["status"])),
        tests_total=int(data["tests_total"]),
        tests_passed=int(data["tests_passed"]),
        tolerance=ValidationTolerance(
            mode=str(tol_data.get("mode", "rtol_atol")),
            rtol=float(tol_data.get("rtol", 0.0)),
            atol=float(tol_data.get("atol", 0.0)),
        ),
        max_abs_error=float(data.get("max_abs_error", 0.0)),
        max_rel_error=float(data.get("max_rel_error", 0.0)),
        failing_cases=failures,
        schema_version=str(data.get("schema_version", "v1")),
        created_at=_parse_datetime(data.get("created_at")),
        content_hash=str(data.get("content_hash", "")),
    )


def benchmark_result_from_dict(data: dict[str, Any]) -> BenchmarkResult:
    timing = data.get("timing", {})
    return BenchmarkResult(
        run_id=str(data["run_id"]),
        candidate_id=str(data["candidate_id"]),
        stage=BenchmarkStage(str(data["stage"])),
        status=BenchmarkStatus(str(data["status"])),
        samples=int(data.get("samples", 0)),
        warmup_iters=int(data.get("warmup_iters", 0)),
        timing=BenchmarkTiming(
            median_us=float(timing.get("median_us", 0.0)),
            p95_us=float(timing.get("p95_us", 0.0)),
            mean_us=float(timing.get("mean_us", 0.0)),
            stdev_us=float(timing.get("stdev_us", 0.0)),
            cov=float(timing.get("cov", 0.0)),
        ),
        env=dict(data.get("env", {})),
        profile=dict(data.get("profile", {})),
        schema_version=str(data.get("schema_version", "v1")),
        created_at=_parse_datetime(data.get("created_at")),
        content_hash=str(data.get("content_hash", "")),
    )


def descriptor_from_dict(data: dict[str, Any]) -> Descriptor:
    return Descriptor(
        run_id=str(data["run_id"]),
        candidate_id=str(data["candidate_id"]),
        descriptor_name=str(data["descriptor_name"]),
        values=dict(data.get("values", {})),
        schema_version=str(data.get("schema_version", "v1")),
        created_at=_parse_datetime(data.get("created_at")),
        content_hash=str(data.get("content_hash", "")),
    )


class RemoteEvaluatorClient:
    def __init__(self, base_url: str, *, timeout_s: float = 120.0) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def evaluate(
        self,
        *,
        problem_id: str,
        candidate: Candidate,
        stage: BenchmarkStage,
        problem_config: dict[str, Any] | None = None,
    ) -> RemoteEvaluationResult:
        payload = {
            "schema_version": "v1",
            "request_id": str(uuid4()),
            "run_id": candidate.run_id,
            "problem_id": problem_id,
            "problem_config": problem_config or {},
            "stage": stage.value,
            "candidate": to_dict(candidate),
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/v1/evaluate",
            method="POST",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as exc:  # pragma: no cover - exercised in integration
            raise RemoteEvaluationError(f"remote eval request failed: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RemoteEvaluationError(f"invalid remote json: {exc}") from exc

        if not data.get("ok", False):
            raise RemoteEvaluationError(str(data.get("error", "remote eval failed")))

        return RemoteEvaluationResult(
            static_check=static_check_from_dict(data["static_check"]),
            build_result=build_result_from_dict(data["build_result"]) if data.get("build_result") else None,
            validation_result=(
                validation_result_from_dict(data["validation_result"]) if data.get("validation_result") else None
            ),
            benchmark_result=(
                benchmark_result_from_dict(data["benchmark_result"]) if data.get("benchmark_result") else None
            ),
            descriptor=descriptor_from_dict(data["descriptor"]) if data.get("descriptor") else None,
            raw_score=data.get("raw_score"),
            scalar_fitness=float(data["scalar_fitness"]) if data.get("scalar_fitness") is not None else None,
        )


class EvalWorkerService:
    def __init__(self, problem_factories: dict[str, Callable[[dict[str, Any] | None], OptimizationProblem]]) -> None:
        self._problem_factories = dict(problem_factories)
        self._problems: dict[str, OptimizationProblem] = {}
        self._lock = threading.Lock()

    def list_problem_ids(self) -> list[str]:
        return sorted(self._problem_factories.keys())

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id", ""))
        try:
            stage = BenchmarkStage(str(payload["stage"]))
            problem_id = str(payload["problem_id"])
            problem_config = payload.get("problem_config", {})
            if not isinstance(problem_config, dict):
                raise TypeError("problem_config must be a dictionary")
            candidate = candidate_from_dict(payload["candidate"])
            if not candidate.content_hash:
                attach_content_hashes(candidate=candidate)
            problem = self._get_problem(problem_id, problem_config)

            static = problem.static_check(candidate)
            response: dict[str, Any] = {
                "schema_version": "v1",
                "request_id": request_id,
                "ok": True,
                "error": None,
                "static_check": to_dict(static),
                "build_result": None,
                "validation_result": None,
                "benchmark_result": None,
                "descriptor": None,
                "raw_score": None,
                "scalar_fitness": None,
            }

            if not static.ok:
                response["raw_score"] = {"fitness": -1e18, "reason": "static_check_failed"}
                response["scalar_fitness"] = -1e18
                return response

            build_exec = problem.build(candidate)
            attach_content_hashes(build_result=build_exec.result)
            response["build_result"] = to_dict(build_exec.result)
            if build_exec.result.status is not BuildStatus.SUCCESS:
                response["raw_score"] = {"fitness": -1e18, "reason": "build_failed"}
                response["scalar_fitness"] = -1e18
                return response

            validation = problem.validate(candidate, build_exec)
            attach_content_hashes(validation_result=validation)
            response["validation_result"] = to_dict(validation)

            if validation.status is not ValidationStatus.PASS:
                raw_score = problem.score(_error_benchmark_result(candidate, stage), validation)
                response["raw_score"] = raw_score
                response["scalar_fitness"] = _scalarize(raw_score)
                return response

            benchmark = problem.benchmark(candidate, build_exec, stage)
            attach_content_hashes(benchmark_result=benchmark)
            response["benchmark_result"] = to_dict(benchmark)

            raw_score = problem.score(benchmark, validation)
            response["raw_score"] = raw_score
            response["scalar_fitness"] = _scalarize(raw_score)

            descriptor = problem.describe(candidate, build_exec, benchmark)
            attach_content_hashes(descriptor=descriptor)
            response["descriptor"] = to_dict(descriptor)
            return response
        except Exception as exc:
            return {
                "schema_version": "v1",
                "request_id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _get_problem(self, problem_id: str, problem_config: dict[str, Any] | None = None) -> OptimizationProblem:
        config_key = json.dumps(problem_config or {}, sort_keys=True, separators=(",", ":"))
        cache_key = f"{problem_id}:{config_key}"
        with self._lock:
            if cache_key in self._problems:
                return self._problems[cache_key]
            if problem_id not in self._problem_factories:
                raise KeyError(f"unknown problem_id: {problem_id}")
            problem = self._problem_factories[problem_id](problem_config)
            self._problems[cache_key] = problem
            return problem


class _EvalWorkerHandler(BaseHTTPRequestHandler):
    server: "EvalWorkerHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "status": "ready",
                "problems": self.server.service.list_problem_ids(),
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/evaluate":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return

        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid content length"})
            return

        raw = self.rfile.read(max(0, length))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid json"})
            return

        response = self.server.service.evaluate(payload)
        status = HTTPStatus.OK if response.get("ok", False) else HTTPStatus.BAD_REQUEST
        self._send_json(status, response)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class EvalWorkerHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], service: EvalWorkerService) -> None:
        super().__init__(server_address, _EvalWorkerHandler)
        self.service = service


class EvalWorkerServer:
    def __init__(self, host: str, port: int, service: EvalWorkerService) -> None:
        self._server = EvalWorkerHTTPServer((host, port), service)

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _scalarize(score: float | dict[str, float]) -> float:
    if isinstance(score, float):
        return score
    if "fitness" in score:
        return float(score["fitness"])
    for value in score.values():
        return float(value)
    return float("-inf")


def _error_benchmark_result(candidate: Candidate, stage: BenchmarkStage) -> BenchmarkResult:
    return BenchmarkResult(
        run_id=candidate.run_id,
        candidate_id=candidate.candidate_id,
        stage=stage,
        status=BenchmarkStatus.ERROR,
        samples=0,
        warmup_iters=0,
        timing=BenchmarkTiming(0.0, 0.0, 0.0, 0.0, 0.0),
        env={"backend": "worker-error-placeholder"},
        profile={},
    )
