from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..hashing import sha256_text
from ..models import Candidate
from ..sdk import ProblemRunContext
from .kernelbench import KernelBenchConfig, KernelBenchProblem

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class YamlProblemSpec:
    """Parsed YAML problem specification."""

    name: str
    pid: str
    ref_source: str
    config: KernelBenchConfig
    description: str
    hardware: str | None
    optimization_hints: str | None
    custom_seeds: list[str]


def _load_yaml_spec(yaml_path: Path) -> YamlProblemSpec:
    """Parse and validate a YAML problem file."""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML problem file must be a mapping, got {type(raw).__name__}")

    name = raw.get("name")
    if not name:
        raise ValueError("YAML problem file must specify 'name'")

    ref_source = raw.get("ref_source", "")
    ref_source_file = raw.get("ref_source_file")
    if not ref_source and ref_source_file:
        ref_path = yaml_path.parent / ref_source_file
        ref_source = ref_path.read_text(encoding="utf-8")
    if not ref_source:
        raise ValueError("YAML problem must specify 'ref_source' or 'ref_source_file'")

    if "class Model" not in ref_source:
        raise ValueError("ref_source must define 'class Model(nn.Module)'")
    if "get_inputs" not in ref_source:
        raise ValueError("ref_source must define 'get_inputs()'")
    if "get_init_inputs" not in ref_source:
        raise ValueError("ref_source must define 'get_init_inputs()'")

    pid = raw.get("problem_id", re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"))

    config_fields: dict[str, Any] = {}
    for field_name in (
        "backend",
        "precision",
        "device",
        "timing_method",
        "seed_count",
        "quick_correct_trials",
        "quick_perf_trials",
        "full_correct_trials",
        "full_perf_trials",
        "build_dir_root",
        "static_check_enabled",
        "static_fail_on_warning",
        "verbose",
    ):
        if field_name in raw:
            config_fields[field_name] = raw[field_name]

    # Dummy level/problem_id since we bypass the KernelBench dataset.
    config_fields["level"] = 0
    config_fields["problem_id"] = 0
    config_fields["dataset_source"] = "local"

    config = KernelBenchConfig.from_dict(config_fields)

    return YamlProblemSpec(
        name=name,
        pid=pid,
        ref_source=ref_source,
        config=config,
        description=raw.get("description", ""),
        hardware=raw.get("hardware"),
        optimization_hints=raw.get("optimization_hints"),
        custom_seeds=raw.get("seeds") or [],
    )


class YamlProblem(KernelBenchProblem):
    """A custom kernel optimization problem loaded from a YAML file.

    Reuses all of KernelBenchProblem's evaluation machinery but injects the
    reference source from the YAML spec instead of loading from the KernelBench
    dataset.
    """

    def __init__(self, spec: YamlProblemSpec) -> None:
        super().__init__(spec.config)
        self._spec = spec

    @classmethod
    def from_yaml_path(cls, yaml_path: str | Path) -> YamlProblem:
        spec = _load_yaml_spec(Path(yaml_path).expanduser().resolve())
        return cls(spec)

    @classmethod
    def from_config_dict(cls, data: dict[str, Any] | None) -> YamlProblem:
        """Factory compatible with ProblemFactory signature."""
        if not data:
            raise ValueError("YamlProblem requires config data")
        if "yaml_path" in data:
            return cls.from_yaml_path(data["yaml_path"])
        if "ref_source" in data:
            pid = data.get("pid", data.get("problem_id", "inline"))
            config_fields = dict(data)
            config_fields["level"] = 0
            config_fields["problem_id"] = 0
            config_fields["dataset_source"] = "local"
            spec = YamlProblemSpec(
                name=data.get("name", "inline_yaml"),
                pid=str(pid),
                ref_source=data["ref_source"],
                config=KernelBenchConfig.from_dict(config_fields),
                description=data.get("description", ""),
                hardware=data.get("hardware"),
                optimization_hints=data.get("optimization_hints"),
                custom_seeds=data.get("seeds") or [],
            )
            return cls(spec)
        raise ValueError("YamlProblem requires 'yaml_path' or 'ref_source' in config")

    def to_config_dict(self) -> dict[str, Any]:
        d = super().to_config_dict()
        d["ref_source"] = self._spec.ref_source
        d["name"] = self._spec.name
        d["pid"] = self._spec.pid
        if self._spec.description:
            d["description"] = self._spec.description
        if self._spec.optimization_hints:
            d["optimization_hints"] = self._spec.optimization_hints
        return d

    def problem_id(self) -> str:
        return f"yaml:{self._spec.pid}"

    def _resolve_reference_source(self, kb_dataset_module: Any) -> tuple[str, str]:
        return self._spec.ref_source, self._spec.name

    def _load_ref_source_from_disk(self) -> tuple[str, str]:
        return self._spec.ref_source, self._spec.name

    def generator_prompt_context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "mode": "yaml_problem",
            "ref_source": self._spec.ref_source,
            "ref_name": self._spec.name,
            "problem_id": self._spec.pid,
            "backend": self.config.backend,
            "precision": self.config.precision,
        }
        if self._spec.hardware:
            ctx["hardware"] = self._spec.hardware
        else:
            ctx["hardware"] = (
                "NVIDIA L40S (Ada Lovelace, 48GB GDDR6 ECC, "
                "735 GB/s memory bandwidth, 181.05 TFLOPS FP32, "
                "362.05 TFLOPS TF32, compute capability 8.9)"
            )
        if self._spec.description:
            ctx["problem_description"] = self._spec.description
        if self._spec.optimization_hints:
            ctx["optimization_hints"] = self._spec.optimization_hints
        return ctx

    @staticmethod
    def _seed_sources_default() -> list[str]:
        return KernelBenchProblem._seed_sources()

    def _seed_sources(self) -> list[str]:
        if self._spec.custom_seeds:
            return self._spec.custom_seeds
        return self._seed_sources_default()

    def _build_dir_for(self, candidate: Candidate) -> Path:
        root = (
            Path(self.config.build_dir_root).expanduser()
            if self.config.build_dir_root
            else Path(tempfile.gettempdir()) / "kernelswarm_yaml_builds"
        )
        stable = candidate.content_hash or sha256_text(self._candidate_source(candidate))
        path = root / self._spec.pid / stable[:24]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _reference_build_dir(self, *, num_correct_trials: int, num_perf_trials: int) -> Path:
        root = (
            Path(self.config.build_dir_root).expanduser()
            if self.config.build_dir_root
            else Path(tempfile.gettempdir()) / "kernelswarm_yaml_builds"
        )
        path = (
            root
            / "reference_baseline"
            / self._spec.pid
            / f"backend_{self.config.backend}"
            / f"precision_{self.config.precision}"
            / f"c{int(num_correct_trials)}_p{int(num_perf_trials)}"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _make_candidate(
        self,
        *,
        run_id: str,
        source: str,
        operation: str,
        agent_id: str,
        hypothesis: str,
    ) -> Candidate:
        from ..models import (
            CandidateOrigin,
            CandidateRepresentation,
            CompileConfig,
            LaunchConfig,
            SourceFile,
        )

        rep = CandidateRepresentation(
            language="python",
            entrypoints=["ModelNew"],
            files=[SourceFile(path="model_new.py", content=source)],
            params={
                "yaml_problem_id": self._spec.pid,
                "yaml_problem_name": self._spec.name,
                "kernelbench_backend": self.config.backend,
                "kernelbench_precision": self.config.precision,
            },
            launch=LaunchConfig(grid=("auto", 1, 1), block=(256, 1, 1), dynamic_smem_bytes=0),
            compile=CompileConfig(arch="auto", flags=[], defines={}),
        )
        return Candidate.new(
            run_id=run_id,
            parent_ids=[],
            origin=CandidateOrigin(island_id="island-a", agent_id=agent_id, operation=operation),
            representation=rep,
            track="from_scratch",
            hypothesis=hypothesis,
        )
