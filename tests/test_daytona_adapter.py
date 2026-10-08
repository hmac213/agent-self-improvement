"""Runs the Daytona backend against a fake `daytona` module whose sandboxes
are local Docker containers. Checks the adapter's own logic (root OS user,
agent user, root-only runtime area, a network-blocked grading sandbox per
task, the network parameters it asks Daytona for); it does not touch
Daytona's cloud, so Daytona's enforcement of those network parameters is not
tested here. Skipped unless Docker and sia-sandbox:latest are available."""

import socket
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

from sia import report, tasks
from sia.config import REPO_ROOT, load
from sia.sandbox.daytona import DaytonaSandbox
from sia.supervisor import Supervisor

from test_docker_isolation import _docker_ready, _launch_probe, _server

pytestmark = pytest.mark.skipif(not _docker_ready(), reason="docker or sia-sandbox:latest image not available")

DOCKERFILE = REPO_ROOT / "sandbox_image" / "Dockerfile"


def _docker(*args, input=None):
    return subprocess.run(["docker", *args], input=input, capture_output=True, timeout=600)


class _FakeSandbox:
    """A Daytona sandbox stood in for by a container. Network parameters map to
    --network none (block all) or host networking (the allow-listed proxy)."""

    def __init__(self, params):
        self.params = params
        self.name = f"fake-daytona-{uuid.uuid4().hex[:8]}"
        self.deleted = False
        image = "sia-sandbox:latest" if params.image == ("dockerfile", DOCKERFILE) else params.image
        net = "none" if getattr(params, "network_block_all", None) else "host"
        p = _docker("run", "-d", "--name", self.name, "--network", net, "--user", params.os_user, image, "sleep", "infinity")
        assert p.returncode == 0, p.stderr
        name = self.name

        class Process:
            def exec(self, command, timeout=None):
                p = subprocess.run(["docker", "exec", name, "sh", "-c", command], capture_output=True, text=True, timeout=timeout)
                return types.SimpleNamespace(exit_code=p.returncode, result=p.stdout + p.stderr)

        class FS:
            def upload_file(self, src, dst, timeout=1800):
                assert _docker("exec", "-i", name, "sh", "-c", 'cat > "$1"', "_", dst, input=src).returncode == 0

            def download_file(self, path):
                return _docker("exec", name, "cat", path).stdout

        self.process, self.fs = Process(), FS()

    def delete(self):
        _docker("rm", "-f", self.name)
        self.deleted = True


@pytest.fixture
def fake_daytona(monkeypatch):
    created = []

    class Daytona:
        def create(self, params, timeout=60):
            sb = _FakeSandbox(params)
            created.append(sb)
            return sb

    mod = types.ModuleType("daytona")
    mod.Daytona = Daytona
    mod.CreateSandboxFromImageParams = lambda **kw: types.SimpleNamespace(**kw)
    mod.Image = types.SimpleNamespace(from_dockerfile=lambda path: ("dockerfile", Path(path)))
    monkeypatch.setitem(sys.modules, "daytona", mod)
    yield created
    for sb in created:
        if not sb.deleted:
            sb.delete()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_daytona_backend_with_fake_sdk(tmp_path, fake_daytona):
    port = _free_port()
    cfg = load(REPO_ROOT / "experiments" / "mock_smoke.toml", {
        "sandbox.kind": "daytona",
        "sandbox.image": "sia-sandbox:latest",
        "sandbox.proxy_port": port,
        "sandbox.proxy_public_url": f"http://127.0.0.1:{port}",
    })
    run_dir = tmp_path / "run"
    summary = Supervisor(cfg, run_dir, verbose=False).run()
    a = report.analyze(run_dir)
    assert summary["mean_score"] == 1.0 and a["signals"]["harness_modified"]
    assert (run_dir / "final" / "agent_home" / "harness" / "tools" / "note.py").exists()
    agent, graders = fake_daytona[0], fake_daytona[1:]
    assert (agent.params.os_user, agent.params.network_allow_list) == ("root", "127.0.0.1/32")
    assert len(graders) == summary["tasks_graded"] == 3
    assert all(g.params.network_block_all and g.params.labels["role"] == "grader" for g in graders)
    assert all(sb.deleted for sb in fake_daytona)


def test_agent_is_confined(fake_daytona):
    proxy, other = _server(b"proxy"), _server(b"other")
    try:
        # An image without the agent user (derived locally, no registry pull): setup creates it.
        b = _docker("build", "-q", "-t", "sia-test-no-agent-user", "-", input=b"FROM sia-sandbox:latest\nRUN userdel -r agent\n")
        assert b.returncode == 0, b.stderr
        sb = DaytonaSandbox("sia-test-no-agent-user", f"http://127.0.0.1:{proxy.server_address[1]}")
        sb.setup()
        try:
            p = _launch_probe(sb, proxy.server_address[1], other.server_address[1])
        finally:
            sb.teardown()
    finally:
        proxy.shutdown()
        other.shutdown()
    assert p["uid"] != 0 and p["proxy"] == "proxy"
    assert not p["runtime_readable"]
    assert p["owns_harness"] and p["can_write"]


def test_hidden_tests_never_enter_the_agent_sandbox(tmp_path, fake_daytona):
    task_dir = tmp_path / "t"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "tests" / "test_t.py").write_text(
        "import socket\nfrom solution import answer\n"
        "def test_answer():\n    assert answer() == 42\n"
        "def test_grader_is_offline():\n"
        "    try:\n        socket.create_connection(('1.1.1.1', 53), 2)\n"
        "    except OSError:\n        return\n    raise AssertionError('grader has network')\n"
    )
    task = tasks.Task("t", task_dir, "# t\n")
    sb = DaytonaSandbox("sia-sandbox:latest", "http://127.0.0.1:1")
    sb.setup()
    try:
        workdir = f"{sb.paths.workspace}/t"
        tasks.prepare_workdir(sb, task, workdir)
        sb.write_bytes(f"{workdir}/solution.py", b"def answer():\n    return 42\n")
        g = tasks.grade(sb, task, workdir)
        assert (g.passed, g.total, g.error) == (2, 2, None), g
        assert sb.exec("find / -xdev -name test_t.py 2>/dev/null")[1].strip() == ""
    finally:
        sb.teardown()
    assert len(fake_daytona) == 2 and fake_daytona[1].params.network_block_all and fake_daytona[1].deleted
