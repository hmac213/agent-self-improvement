"""Runs the harness as a plain host process under the run directory.

NOT isolated: the agent has the same access as the user running the
supervisor. Use it for development and mock runs; use docker or daytona for
real experiments.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .base import Sandbox, SandboxPaths


class LocalSandbox(Sandbox):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.paths = SandboxPaths(
            agent_home=str(self.root / "agent"),
            env_dir=str(self.root / "env"),
            workspace=str(self.root / "workspace"),
            runtime=str(self.root / "runtime"),
        )
        self.python = sys.executable

    def setup(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.make_dirs()

    def teardown(self) -> None:
        self.stop_harness()

    def exec(self, cmd: str, timeout: float = 120) -> tuple[int, str]:
        try:
            p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired:
            return 124, "timeout"

    def write_bytes(self, path: str, data: bytes) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)

    def read_bytes(self, path: str) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None

    def proxy_url(self, port: int) -> str:
        return f"http://127.0.0.1:{port}"

    def destroy(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
