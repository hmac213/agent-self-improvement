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
    # When set, the harness runs as this unprivileged user and owns the agent's
    # directories, while the supervisor's own commands (exec) keep running as
    # root and the runtime area is root-only.
    agent_user: str | None = None

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
        agent_dirs = " ".join(shlex.quote(d) for d in (p.agent_home, p.env_dir, p.workspace))
        self.check(f"mkdir -p {agent_dirs} {shlex.quote(p.runtime)}")
        if self.agent_user:
            u = shlex.quote(self.agent_user)
            self.check(f"chown -R {u}:{u} {agent_dirs} && chmod 700 {shlex.quote(p.runtime)}")

    def give_to_agent(self, path: str) -> None:
        """Make `path` (recursively) owned by the agent user, if there is one."""
        if self.agent_user:
            u = shlex.quote(self.agent_user)
            self.check(f"chown -R {u}:{u} {shlex.quote(path)}")

    def pack_dir(self, remote: str, exclude: tuple[str, ...] = ()) -> bytes | None:
        """A sandbox directory as a .tar.gz, or None if it doesn't exist."""
        tmp = f"{self.paths.runtime}/download.tgz"
        excl = " ".join(f"--exclude={shlex.quote(e)}" for e in exclude)
        code, _ = self.exec(f"test -d {shlex.quote(remote)} && tar -czf {tmp} {excl} -C {shlex.quote(remote)} .", timeout=300)
        if code != 0:
            return None
        data = self.read_bytes(tmp)
        self.exec(f"rm -f {tmp}")
        return data

    def unpack_dir(self, data: bytes, remote: str, replace: bool = False) -> None:
        """Extract a .tar.gz (from pack_dir or upload_dir) into a sandbox directory.
        Anything outside the runtime area is handed to the agent user."""
        tmp = f"{self.paths.runtime}/upload.tgz"
        self.write_bytes(tmp, data)
        q = shlex.quote(remote)
        rm = f"rm -rf {q} && " if replace else ""
        self.check(f"{rm}mkdir -p {q} && tar -xzf {tmp} -C {q} && rm -f {tmp}")
        if not remote.startswith(self.paths.runtime + "/"):
            self.give_to_agent(remote)

    def upload_dir(self, local: Path, remote: str, replace: bool = False) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(local, arcname=".")
        self.unpack_dir(buf.getvalue(), remote, replace)

    def download_dir(self, remote: str, local: Path, exclude: tuple[str, ...] = ()) -> bool:
        """Copy a sandbox directory to the host. Returns False if it doesn't exist."""
        data = self.pack_dir(remote, exclude)
        if data is None:
            return False
        local.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            tar.extractall(local, filter="data")
        return True

    def grading_sandbox(self) -> "Sandbox | None":
        """A fresh, not yet set up sandbox to grade one submission in, isolated
        from the agent; None means grade in this sandbox (tasks.grade)."""
        return None

    # --- harness process ------------------------------------------------------
    def start_harness(self, env: dict[str, str], log_path: str) -> None:
        """Launch <harness>/start.sh in its own process group, detached.

        A wrapper records the exit code in <runtime>/exit_code so poll_harness
        works the same on every backend.
        """
        rt = self.paths.runtime
        envs = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
        # The wrapper shell keeps the supervisor's identity so it can write the
        # log and exit code into the runtime area; only the harness drops to the agent user.
        drop = ""
        if self.agent_user:
            u = shlex.quote(self.agent_user)
            drop = f"setpriv --reuid={u} --regid={u} --init-groups "
        inner = (
            f"cd {shlex.quote(self.paths.agent_home)}; "
            f"{drop}env {envs} sh {shlex.quote(self.paths.harness)}/start.sh > {shlex.quote(log_path)} 2>&1; "
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
