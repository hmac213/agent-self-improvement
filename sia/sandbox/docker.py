"""One long-lived Docker container per run. Build the default image with
`docker build -t sia-sandbox:latest sandbox_image/`."""

from __future__ import annotations

import subprocess
import uuid

from .base import Sandbox, SandboxPaths


class DockerSandbox(Sandbox):
    def __init__(self, image: str, name: str | None = None):
        self.image = image
        self.name = name or f"sia-{uuid.uuid4().hex[:10]}"
        self.paths = SandboxPaths(agent_home="/agent", env_dir="/env", workspace="/workspace", runtime="/var/lib/sia")

    def _docker(self, *args: str, input: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout)

    def setup(self) -> None:
        p = self._docker(
            "run", "-d", "--name", self.name,
            "--add-host", "host.docker.internal:host-gateway",
            "-e", "HOME=/agent",
            self.image, "sleep", "infinity",
            timeout=600,
        )
        if p.returncode != 0:
            raise RuntimeError(f"docker run failed: {p.stderr.decode()}")
        self.make_dirs()

    def teardown(self) -> None:
        self._docker("rm", "-f", self.name, timeout=60)

    def exec(self, cmd: str, timeout: float = 120) -> tuple[int, str]:
        try:
            p = self._docker("exec", self.name, "sh", "-c", cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        return p.returncode, (p.stdout + p.stderr).decode(errors="replace")

    def write_bytes(self, path: str, data: bytes) -> None:
        p = self._docker("exec", "-i", self.name, "sh", "-c", f'mkdir -p "$(dirname "$1")" && cat > "$1"', "_", path, input=data)
        if p.returncode != 0:
            raise RuntimeError(f"write {path} failed: {p.stderr.decode()}")

    def read_bytes(self, path: str) -> bytes | None:
        p = self._docker("exec", self.name, "sh", "-c", 'test -f "$1" && cat "$1"', "_", path, timeout=300)
        return p.stdout if p.returncode == 0 else None

    def proxy_url(self, port: int) -> str:
        return f"http://host.docker.internal:{port}"
