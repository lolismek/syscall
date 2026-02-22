from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Iterable


class BrevError(RuntimeError):
    pass


@dataclass(slots=True)
class BrevInstance:
    name: str
    status: str
    build: str
    shell: str
    instance_id: str
    machine: str

    @property
    def is_running(self) -> bool:
        return self.status.upper() == "RUNNING"

    @property
    def shell_ready(self) -> bool:
        return self.shell.upper() == "READY"


class BrevClient:
    def __init__(self, binary: str = "brev") -> None:
        self.binary = binary

    def list_instances(self) -> list[BrevInstance]:
        result = self._run([self.binary, "ls"])
        if result.returncode != 0:
            raise BrevError(f"brev ls failed: {result.stderr.strip() or result.stdout.strip()}")
        return self._parse_ls(result.stdout)

    def get_instance(self, name: str) -> BrevInstance | None:
        for instance in self.list_instances():
            if instance.name == name:
                return instance
        return None

    def create_instance(self, *, name: str, machine: str) -> None:
        result = self._run([self.binary, "create", name, "--gpu", machine, "--detached"])
        if result.returncode != 0:
            raise BrevError(f"brev create failed: {result.stderr.strip() or result.stdout.strip()}")

    def ensure_instance(
        self,
        *,
        name: str,
        machine: str,
        create_if_missing: bool,
        wait_timeout_s: float = 600.0,
    ) -> BrevInstance:
        instance = self.get_instance(name)
        if instance is None:
            if not create_if_missing:
                raise BrevError(f"Brev instance {name!r} not found")
            self.create_instance(name=name, machine=machine)

        deadline = time.time() + max(1.0, wait_timeout_s)
        while True:
            instance = self.get_instance(name)
            if instance is None:
                raise BrevError(f"Brev instance {name!r} disappeared while waiting")
            if instance.is_running and instance.shell_ready:
                return instance
            if time.time() >= deadline:
                raise BrevError(
                    f"Timed out waiting for Brev instance {name!r} to become READY; "
                    f"status={instance.status}, shell={instance.shell}"
                )
            time.sleep(5.0)

    def start_port_forward(
        self,
        *,
        name: str,
        local_port: int,
        remote_port: int,
    ) -> subprocess.Popen[str]:
        cmd = [self.binary, "port-forward", name, "-p", f"{local_port}:{remote_port}"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # noqa: S603

    @staticmethod
    def _parse_ls(output: str) -> list[BrevInstance]:
        lines = [line.rstrip() for line in output.splitlines() if line.strip()]
        # Skip banner and header.
        header_idx = None
        for idx, line in enumerate(lines):
            if re.search(r"\bNAME\b", line) and re.search(r"\bSTATUS\b", line):
                header_idx = idx
                break
        if header_idx is None:
            return []

        instances: list[BrevInstance] = []
        for row in lines[header_idx + 1 :]:
            if row.lstrip().startswith("-"):
                continue
            parts = re.split(r"\s{2,}", row.strip())
            if len(parts) < 6:
                continue
            name, status, build, shell, instance_id, machine = parts[:6]
            instances.append(
                BrevInstance(
                    name=name,
                    status=status,
                    build=build,
                    shell=shell,
                    instance_id=instance_id,
                    machine=machine,
                )
            )
        return instances

    @staticmethod
    def _run(cmd: Iterable[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            list(cmd),
            check=False,
            capture_output=True,
            text=True,
        )
