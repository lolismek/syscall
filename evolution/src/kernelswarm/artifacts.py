from __future__ import annotations

from pathlib import Path
from typing import Any

from .serialization import to_json


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def candidate_dir(self, run_id: str, candidate_id: str, kind: str) -> Path:
        directory = self.run_dir(run_id) / "candidates" / candidate_id / kind
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_json(self, path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_json(payload, indent=2), encoding="utf-8")
        return path

    def write_text(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, path: Path, data: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path
