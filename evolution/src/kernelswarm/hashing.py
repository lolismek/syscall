from __future__ import annotations

import hashlib
from typing import Any

from .models import (
    BenchmarkResult,
    BuildResult,
    Candidate,
    Descriptor,
    IterationMetric,
    ScoreRecord,
    ValidationResult,
)
from .serialization import to_json


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_object_hash(value: Any) -> str:
    return sha256_text(to_json(value))


def candidate_content_hash(candidate: Candidate) -> str:
    # Candidate identity hash should ignore volatile metadata and IDs.
    payload = {
        "schema_version": candidate.schema_version,
        "representation": candidate.representation,
        "track": candidate.track,
        "hypothesis": candidate.hypothesis,
    }
    return stable_object_hash(payload)


def attach_content_hashes(
    *,
    candidate: Candidate | None = None,
    build_result: BuildResult | None = None,
    validation_result: ValidationResult | None = None,
    benchmark_result: BenchmarkResult | None = None,
    descriptor: Descriptor | None = None,
    score_record: ScoreRecord | None = None,
    iteration_metric: IterationMetric | None = None,
) -> None:
    if candidate is not None:
        candidate.content_hash = candidate_content_hash(candidate)
    if build_result is not None:
        build_result.content_hash = stable_object_hash(
            {
                "schema_version": build_result.schema_version,
                "candidate_id": build_result.candidate_id,
                "status": build_result.status,
                "build_backend": build_result.build_backend,
                "artifacts": build_result.artifacts,
                "compiler_metrics": build_result.compiler_metrics,
                "toolchain_fingerprint": build_result.toolchain_fingerprint,
            }
        )
    if validation_result is not None:
        validation_result.content_hash = stable_object_hash(
            {
                "schema_version": validation_result.schema_version,
                "candidate_id": validation_result.candidate_id,
                "status": validation_result.status,
                "tests_total": validation_result.tests_total,
                "tests_passed": validation_result.tests_passed,
                "max_abs_error": validation_result.max_abs_error,
                "max_rel_error": validation_result.max_rel_error,
                "failing_cases": validation_result.failing_cases,
            }
        )
    if benchmark_result is not None:
        benchmark_result.content_hash = stable_object_hash(
            {
                "schema_version": benchmark_result.schema_version,
                "candidate_id": benchmark_result.candidate_id,
                "stage": benchmark_result.stage,
                "status": benchmark_result.status,
                "timing": benchmark_result.timing,
                "env": benchmark_result.env,
            }
        )
    if descriptor is not None:
        descriptor.content_hash = stable_object_hash(
            {
                "schema_version": descriptor.schema_version,
                "candidate_id": descriptor.candidate_id,
                "descriptor_name": descriptor.descriptor_name,
                "values": descriptor.values,
            }
        )
    if score_record is not None:
        score_record.content_hash = stable_object_hash(
            {
                "schema_version": score_record.schema_version,
                "candidate_id": score_record.candidate_id,
                "stage": score_record.stage,
                "scalar_fitness": score_record.scalar_fitness,
                "raw_score": score_record.raw_score,
            }
        )
    if iteration_metric is not None:
        iteration_metric.content_hash = stable_object_hash(
            {
                "schema_version": iteration_metric.schema_version,
                "run_id": iteration_metric.run_id,
                "iteration": iteration_metric.iteration,
                "island_id": iteration_metric.island_id,
                "candidate_id": iteration_metric.candidate_id,
                "quick_fitness": iteration_metric.quick_fitness,
                "full_fitness": iteration_metric.full_fitness,
                "quick_median_us": iteration_metric.quick_median_us,
                "full_median_us": iteration_metric.full_median_us,
                "island_top_fitness": iteration_metric.island_top_fitness,
                "island_coverage_ratio": iteration_metric.island_coverage_ratio,
                "island_occupied_bins": iteration_metric.island_occupied_bins,
                "island_accepted_updates": iteration_metric.island_accepted_updates,
                "global_best_candidate_id": iteration_metric.global_best_candidate_id,
                "global_best_fitness": iteration_metric.global_best_fitness,
                "total_tokens": iteration_metric.total_tokens,
                "payload": iteration_metric.payload,
            }
        )
