"""One long-lived Docker container per run. Build the default image with
`docker build -t sia-sandbox:latest sandbox_image/`.

Isolation (the image must have an `agent` user, as sandbox_image/ does):
  * the harness runs as `agent`; supervisor commands run as root, and the
    runtime area (/var/lib/sia: harness logs, exit codes, transfers) is root-only;
  * every task is graded in a fresh container with no network, so the hidden
    tests never exist in the agent's container;
  * network = "proxy_only" (the default) puts the container on an internal
    Docker network whose only other member is a small TCP forwarder to the
    host's LLM proxy: no internet, no other host services.
    network = "open" keeps Docker's default bridge with access to the host.
"""

from __future__ import annotations

import subprocess
import time
import uuid

from .base import Sandbox, SandboxPaths

PROXY_ALIAS = "sia-proxy"  # the forwarder's name on the run's internal network
FORWARDER_PORT = 8080

# Run in the forwarder container: relay every TCP connection on FORWARDER_PORT
# to the host's LLM proxy, so the internal network needs no route to the host.
FORWARDER = r"""
import asyncio, sys
HOST, PORT, LISTEN = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
        if w.can_write_eof():
            w.write_eof()
    except Exception:
        pass

async def handle(r, w):
    try:
        ur, uw = await asyncio.open_connection(HOST, PORT)
    except Exception:
        w.close()
        return
    await asyncio.gather(pipe(r, uw), pipe(ur, w))
    uw.close()
    w.close()

async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", LISTEN)
    async with server:
        await server.serve_forever()

asyncio.run(main())
"""

# What root inside a container still needs: chown/chmod and writes in files it
# doesn't own (tar and cp -a keep owners), plus, where the harness runs,
# dropping to the agent user and killing its processes.
FILE_CAPS = ("CHOWN", "DAC_OVERRIDE", "FOWNER")
AGENT_CAPS = (*FILE_CAPS, "SETUID", "SETGID", "KILL")
NETWORKS = ("proxy_only", "open", "none")


class DockerSandbox(Sandbox):
    def __init__(self, image: str, name: str | None = None, network: str = "proxy_only",
                 proxy_port: int | None = None, agent_user: str | None = "agent"):
        if network not in NETWORKS:
            raise ValueError(f"unknown docker network mode {network!r}; known: {', '.join(NETWORKS)}")
        self.image = image
        self.name = name or f"sia-{uuid.uuid4().hex[:10]}"
        self.network = network
        self.proxy_port = proxy_port
        self.agent_user = agent_user
        self.net_name = f"{self.name}-net"
        self.forwarder_name = f"{self.name}-proxy"
        self.paths = SandboxPaths(agent_home="/agent", env_dir="/env", workspace="/workspace", runtime="/var/lib/sia")

    def _docker(self, *args: str, input: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], input=input, capture_output=True, timeout=timeout)

    def _docker_ok(self, *args: str, timeout: float = 120) -> None:
        p = self._docker(*args, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError(f"docker {args[0]} failed: {p.stderr.decode()}")

    def setup(self) -> None:
        run = ["run", "-d", "--name", self.name, "-e", "HOME=/agent", "--security-opt", "no-new-privileges"]
        if self.network == "open":
            run += ["--add-host", "host.docker.internal:host-gateway"]
        elif self.network == "none":
            run += ["--network", "none"]
        else:
            if not self.proxy_port:
                raise ValueError("network = 'proxy_only' needs the host proxy's port")
            self._start_forwarder()
            run += ["--network", self.net_name]
        caps = AGENT_CAPS if self.agent_user else FILE_CAPS
        run += ["--cap-drop", "ALL", *(a for c in caps for a in ("--cap-add", c))]
        p = self._docker(*run, self.image, "sleep", "infinity", timeout=600)
        if p.returncode != 0:
            raise RuntimeError(f"docker run failed: {p.stderr.decode()}")
        if self.agent_user and self.exec(f"id -u {self.agent_user}")[0] != 0:
            raise RuntimeError(f"image {self.image} has no user {self.agent_user!r}; rebuild it from sandbox_image/")
        self.make_dirs()
        if self.network == "proxy_only":
            self._wait_for_forwarder()

    def _start_forwarder(self) -> None:
        self._docker_ok("network", "create", "--internal", self.net_name)
        self._docker_ok(
            "run", "-d", "--name", self.forwarder_name,
            "--add-host", "host.docker.internal:host-gateway",
            "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            self.image, "python3", "-c", FORWARDER, "host.docker.internal", str(self.proxy_port), str(FORWARDER_PORT),
            timeout=600,
        )
        self._docker_ok("network", "connect", "--alias", PROXY_ALIAS, self.net_name, self.forwarder_name)

    def _wait_for_forwarder(self, timeout: float = 30) -> None:
        probe = (
            f"{self.python} -c \"import socket; socket.create_connection(('{PROXY_ALIAS}', {FORWARDER_PORT}), 2).close()\""
        )
        deadline = time.time() + timeout
        while True:
            code, out = self.exec(probe, timeout=10)
            if code == 0:
                return
            if time.time() > deadline:
                raise RuntimeError(f"sandbox can't reach the proxy forwarder: {out[-500:]}")
            time.sleep(0.5)

    def teardown(self) -> None:
        self._docker("rm", "-f", self.name, timeout=60)
        if self.network == "proxy_only":
            self._docker("rm", "-f", self.forwarder_name, timeout=60)
            self._docker("network", "rm", self.net_name, timeout=60)

    def exec(self, cmd: str, timeout: float = 120) -> tuple[int, str]:
        try:
            p = self._docker("exec", "-u", "0", self.name, "sh", "-c", cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        return p.returncode, (p.stdout + p.stderr).decode(errors="replace")

    def write_bytes(self, path: str, data: bytes) -> None:
        p = self._docker("exec", "-i", "-u", "0", self.name, "sh", "-c", f'mkdir -p "$(dirname "$1")" && cat > "$1"', "_", path, input=data)
        if p.returncode != 0:
            raise RuntimeError(f"write {path} failed: {p.stderr.decode()}")

    def read_bytes(self, path: str) -> bytes | None:
        p = self._docker("exec", "-u", "0", self.name, "sh", "-c", 'test -f "$1" && cat "$1"', "_", path, timeout=300)
        return p.stdout if p.returncode == 0 else None

    def proxy_url(self, port: int) -> str:
        if self.network == "proxy_only":
            return f"http://{PROXY_ALIAS}:{FORWARDER_PORT}"
        return f"http://host.docker.internal:{port}"

    def grading_sandbox(self) -> "DockerSandbox":
        """A throwaway container with no network and no agent user."""
        return DockerSandbox(self.image, name=f"{self.name}-grade-{uuid.uuid4().hex[:6]}", network="none", agent_user=None)
