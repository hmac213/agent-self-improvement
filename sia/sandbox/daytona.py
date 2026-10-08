"""Daytona cloud sandbox backend (pip install daytona; set DAYTONA_API_KEY).

The harness inside a Daytona sandbox must reach the supervisor's LLM proxy
over the internet, so `sandbox.proxy_public_url` is required: expose the
proxy port with a tunnel (e.g. `cloudflared tunnel --url http://localhost:PORT`)
or run the supervisor on a host with a public address. Requests are
authenticated with a per-run token.
"""

from __future__ import annotations

from .base import Sandbox, SandboxPaths


class DaytonaSandbox(Sandbox):
    def __init__(self, image: str, proxy_public_url: str | None, network_allow_list: str | None = None, labels: dict | None = None):
        if not proxy_public_url:
            raise ValueError("daytona sandboxes need sandbox.proxy_public_url (see sia/sandbox/daytona.py)")
        self.image = image
        self.public_url = proxy_public_url.rstrip("/")
        self.network_allow_list = network_allow_list
        self.labels = labels or {}
        self.paths = SandboxPaths(agent_home="", env_dir="", workspace="", runtime="")  # set in setup()
        self.sb = None

    def setup(self) -> None:
        from daytona import CreateSandboxFromImageParams, Daytona

        self.client = Daytona()
        params = CreateSandboxFromImageParams(
            image=self.image,
            labels={"sia": "1", **self.labels},
            auto_stop_interval=0,
            network_allow_list=self.network_allow_list,
        )
        self.sb = self.client.create(params, timeout=600)
        # Everything lives under the sandbox user's home so no root is needed.
        base = self.sb.get_user_home_dir().rstrip("/")
        self.paths = SandboxPaths(
            agent_home=f"{base}/agent", env_dir=f"{base}/env", workspace=f"{base}/workspace", runtime=f"{base}/.sia"
        )
        self.make_dirs()

    def teardown(self) -> None:
        if self.sb is not None:
            self.sb.delete()
            self.sb = None

    def exec(self, cmd: str, timeout: float = 120) -> tuple[int, str]:
        import shlex

        r = self.sb.process.exec(f"sh -c {shlex.quote(cmd)}", timeout=int(timeout))
        return r.exit_code, r.result or ""

    def write_bytes(self, path: str, data: bytes) -> None:
        import shlex
        import posixpath

        self.exec(f"mkdir -p {shlex.quote(posixpath.dirname(path))}")
        self.sb.fs.upload_file(data, path)

    def read_bytes(self, path: str) -> bytes | None:
        import shlex

        code, _ = self.exec(f"test -f {shlex.quote(path)}")
        if code != 0:
            return None
        return self.sb.fs.download_file(path)

    def proxy_url(self, port: int) -> str:
        return self.public_url
