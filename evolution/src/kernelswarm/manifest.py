from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .models import RunManifest


def _capture_cmd(cmd: list[str]) -> str | None:
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip() or completed.stderr.strip()
    return output.splitlines()[0] if output else None


def detect_toolchain() -> dict[str, str]:
    toolchain: dict[str, str] = {}

    nvcc_path = shutil.which("nvcc")
    if nvcc_path:
        toolchain["nvcc_path"] = nvcc_path
        version = _capture_cmd(["nvcc", "--version"])
        if version:
            toolchain["nvcc_version"] = version

    nvidia_smi_path = shutil.which("nvidia-smi")
    if nvidia_smi_path:
        toolchain["nvidia_smi_path"] = nvidia_smi_path
        gpu_name = _capture_cmd([
            "nvidia-smi",
            "--query-gpu=name",
            "--format=csv,noheader",
        ])
        if gpu_name:
            toolchain["gpu_name"] = gpu_name

        driver = _capture_cmd([
            "nvidia-smi",
            "--query-gpu=driver_version",
            "--format=csv,noheader",
        ])
        if driver:
            toolchain["driver_version"] = driver

    return toolchain


def detect_git_commit(repo_root: Path) -> str | None:
    return _capture_cmd(["git", "-C", str(repo_root), "rev-parse", "HEAD"])


def build_run_manifest(*, run_id: str, problem_id: str, seed: int, repo_root: Path) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        problem_id=problem_id,
        seed=seed,
        python_version=sys.version.replace("\n", " "),
        platform=platform.platform(),
        toolchain=detect_toolchain(),
        git_commit=detect_git_commit(repo_root),
    )
