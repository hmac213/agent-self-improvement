"""Daytona cloud sandbox backend (pip install daytona; set DAYTONA_API_KEY).

The harness inside a Daytona sandbox must reach the supervisor's LLM proxy
over the internet, so `sandbox.proxy_public_url` is required: expose the
proxy port with a tunnel (e.g. `cloudflared tunnel --url http://localhost:PORT`)
or run the supervisor on a host with a public address. Requests are
authenticated with a per-run token.

Isolation matches the docker backend:
  * the sandbox's OS user is root, which only the supervisor's commands use;
    the harness runs as the unprivileged `agent` user (created if the image
    lacks it) and the runtime area (/var/lib/sia) is root-only;
  * every task is graded in a fresh sandbox with all network blocked, so the
    hidden tests never exist in the agent's sandbox;
  * network = "proxy_only" (the default) lets the sandbox reach only the
    proxy's host: a domain allow-list for a hostname, a /32 for an IP
    address (or `sandbox.daytona_network_allow_list` if set).
    network = "open" applies only `daytona_network_allow_list`, if any.
The default image ("sia-sandbox:latest") is built by Daytona from
sandbox_image/Dockerfile; any other value is used as a registry image.
"""

from __future__ import annotations

import ipaddress
import shlex
from urllib.parse import urlparse

from ..config import REPO_ROOT
from .base import Sandbox, SandboxPaths

DEFAULT_IMAGE = "sia-sandbox:latest"
NETWORKS = ("proxy_only", "open", "none")


class DaytonaSandbox(Sandbox):
    def __init__(self, image: str, proxy_public_url: str | None, network_allow_list: str | None = None,
                 labels: dict | None = None, network: str = "proxy_only", agent_user: str | None = "agent"):
        if network not in NETWORKS:
            raise ValueError(f"unknown daytona network mode {network!r}; known: {', '.join(NETWORKS)}")
        if not proxy_public_url and network != "none":
            raise ValueError("daytona sandboxes need sandbox.proxy_public_url (see sia/sandbox/daytona.py)")
        self.image = image
        self.public_url = (proxy_public_url or "").rstrip("/")
        self.network_allow_list = network_allow_list
        self.labels = labels or {}
        self.network = network
        self.agent_user = agent_user
        self.paths = SandboxPaths(agent_home="/agent", env_dir="/env", workspace="/workspace", runtime="/var/lib/sia")
        self.sb = None

    def _image(self):
        if self.image != DEFAULT_IMAGE:
            return self.image
        from daytona import Image

        return Image.from_dockerfile(REPO_ROOT / "sandbox_image" / "Dockerfile")

    def network_params(self) -> dict:
        if self.network == "none":
            return {"network_block_all": True}
        if self.network == "open" or self.network_allow_list:
            return {"network_allow_list": self.network_allow_list}
        host = urlparse(self.public_url).hostname or ""
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return {"domain_allow_list": host}
        return {"network_allow_list": f"{ip}/{ip.max_prefixlen}"}

    def setup(self) -> None:
        from daytona import CreateSandboxFromImageParams, Daytona

        self.client = Daytona()
        params = CreateSandboxFromImageParams(
            image=self._image(),
            labels={"sia": "1", **self.labels},
            os_user="root",
            auto_stop_interval=0,
            **self.network_params(),
        )
        self.sb = self.client.create(params, timeout=600)
        code, out = self.exec("id -u")
        if code != 0 or out.strip() != "0":
            raise RuntimeError(f"daytona sandbox commands must run as root, got uid {out.strip()!r}")
        if self.agent_user:
            u = shlex.quote(self.agent_user)
            self.check(
                f"command -v setpriv >/dev/null || {{ echo 'image has no setpriv (util-linux)'; exit 1; }}; "
                f"id -u {u} >/dev/null 2>&1 || useradd --home-dir {shlex.quote(self.paths.agent_home)} --create-home --shell /bin/sh {u}"
            )
        self.make_dirs()

    def teardown(self) -> None:
        if self.sb is not None:
            self.sb.delete()
            self.sb = None

    def exec(self, cmd: str, timeout: float = 120) -> tuple[int, str]:
        r = self.sb.process.exec(f"sh -c {shlex.quote(cmd)}", timeout=int(timeout))
        return r.exit_code, r.result or ""

    def write_bytes(self, path: str, data: bytes) -> None:
        import posixpath

        self.exec(f"mkdir -p {shlex.quote(posixpath.dirname(path))}")
        self.sb.fs.upload_file(data, path)

    def read_bytes(self, path: str) -> bytes | None:
        code, _ = self.exec(f"test -f {shlex.quote(path)}")
        if code != 0:
            return None
        return self.sb.fs.download_file(path)

    def proxy_url(self, port: int) -> str:
        return self.public_url

    def grading_sandbox(self) -> "DaytonaSandbox":
        """A fresh sandbox from the same image, with all network blocked and no agent user."""
        return DaytonaSandbox(self.image, None, labels={**self.labels, "role": "grader"}, network="none", agent_user=None)
