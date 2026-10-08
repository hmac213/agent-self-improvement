"""Sandbox interface.

A backend only has to implement `exec`, `write_bytes`, `read_bytes`, `setup`
and `teardown`. Directory transfer and harness process management are built on
those here, so every backend runs the harness the same way.
"""

from __future__ import annotations

import io
import shlex
import tarfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxPaths:
    agent_home: str  # persistent home; the harness lives in <agent_home>/harness
    env_dir: str  # mailbox shared with the supervisor
    workspace: str  # one subdirectory per task
    runtime: str  # supervisor scratch (harness logs, exit codes, grading)

    @property
    def harness(self) -> str:
        return f"{self.agent_home}/harness"


class Sandbox(ABC):
    paths: SandboxPaths
    python: str = "python3"

    # --- backend primitives -------------------------------------------------
    @abstractmethod
    def setup(self) -> None: ...

    @abstractmethod
    def teardown(self) -> None: ...

    @abstractmethod
    def exec(self, cmd: str, timeout: float = 120) -> tuple[int, str]:
        """Run `cmd` with sh; return (exit code, combined output)."""

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, path: str) -> bytes | None:
        """Return file contents, or None if it doesn't exist."""

    @abstractmethod
    def proxy_url(self, port: int) -> str:
        """URL at which code inside the sandbox reaches the host's LLM proxy."""

    # --- helpers --------------------------------------------------------------
    def check(self, cmd: str, timeout: float = 120) -> str:
        code, out = self.exec(cmd, timeout)
        if code != 0:
            raise RuntimeError(f"sandbox command failed ({code}): {cmd}\n{out}")
        return out

    def write_text_atomic(self, path: str, text: str) -> None:
        tmp = f"{path}.sia-tmp"
        self.write_bytes(tmp, text.encode())
        self.check(f"mv -f {shlex.quote(tmp)} {shlex.quote(path)}")

    def read_text(self, path: str) -> str | None:
        data = self.read_bytes(path)
        return None if data is None else data.decode(errors="replace")

    def make_dirs(self) -> None:
        p = self.paths
        self.check("mkdir -p " + " ".join(shlex.quote(d) for d in (p.agent_home, p.env_dir, p.workspace, p.runtime)))

    def upload_dir(self, local: Path, remote: str, replace: bool = False) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(local, arcname=".")
        tmp = f"{self.paths.runtime}/upload.tgz"
        self.write_bytes(tmp, buf.getvalue())
        q = shlex.quote(remote)
        rm = f"rm -rf {q} && " if replace else ""
        self.check(f"{rm}mkdir -p {q} && tar -xzf {tmp} -C {q} && rm -f {tmp}")

    def download_dir(self, remote: str, local: Path, exclude: tuple[str, ...] = ()) -> bool:
        """Copy a sandbox directory to the host. Returns False if it doesn't exist."""
        tmp = f"{self.paths.runtime}/download.tgz"
        excl = " ".join(f"--exclude={shlex.quote(e)}" for e in exclude)
        code, _ = self.exec(f"test -d {shlex.quote(remote)} && tar -czf {tmp} {excl} -C {shlex.quote(remote)} .", timeout=300)
        if code != 0:
            return False
        data = self.read_bytes(tmp)
        self.exec(f"rm -f {tmp}")
        local.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(local, filter="data")
        return True

    # --- harness process ------------------------------------------------------
    def start_harness(self, env: dict[str, str], log_path: str) -> None:
        """Launch <harness>/start.sh in its own process group, detached.

        A wrapper records the exit code in <runtime>/exit_code so poll_harness
        works the same on every backend.
        """
        rt = self.paths.runtime
        envs = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        inner = (
            f"cd {shlex.quote(self.paths.agent_home)}; "
            f"env {envs} sh {shlex.quote(self.paths.harness)}/start.sh > {shlex.quote(log_path)} 2>&1; "
            f"echo $? > {rt}/exit_code"
        )
        cmd = (
            f"rm -f {rt}/exit_code; "
            f"setsid sh -c {shlex.quote(inner)} > /dev/null 2>&1 < /dev/null & echo $! > {rt}/harness.pid"
        )
        self.check(cmd)

    def poll_harness(self) -> int | None:
        """Exit code if the harness has exited, else None."""
        rt = self.paths.runtime
        out = self.check(
            f"if [ -f {rt}/exit_code ]; then cat {rt}/exit_code; "
            f"elif kill -0 $(cat {rt}/harness.pid) 2>/dev/null; then echo running; "
            f"else echo lost; fi"
        ).strip()
        if out == "running":
            return None
        if out == "lost" or not out:
            return -1
        return int(out.splitlines()[-1])

    def stop_harness(self) -> None:
        rt = self.paths.runtime
        self.exec(
            f"pid=$(cat {rt}/harness.pid 2>/dev/null) && "
            f"(kill -TERM -- -$pid 2>/dev/null; sleep 2; kill -KILL -- -$pid 2>/dev/null); true"
        )
