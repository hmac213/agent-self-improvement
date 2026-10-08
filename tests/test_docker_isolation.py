"""The docker sandbox keeps the agent away from what it must not see or reach.

Runs real containers (skipped unless Docker and sia-sandbox:latest exist).
Probes run inside the launched harness, i.e. with exactly the identity and
network the agent gets. The host-service check has a control: the same
service is reachable from a container on the open network, so it can't pass
just because the machine has no network at all.
"""

import http.server
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from sia import tasks
from sia.sandbox.docker import DockerSandbox


def _docker_ready():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", "sia-sandbox:latest"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="docker or sia-sandbox:latest image not available")


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.server.reply
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _server(reply: bytes):
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _Ok)
    srv.reply = reply
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def servers():
    proxy, other = _server(b"proxy"), _server(b"other host service")
    yield proxy.server_address[1], other.server_address[1]
    proxy.shutdown()
    other.shutdown()


PROBE = r"""
import json, os, socket, urllib.request
def get(url):
    try:
        return urllib.request.urlopen(url, timeout=3).read().decode()
    except Exception as e:
        return "ERR " + type(e).__name__
def readable(path):
    try:
        os.listdir(path) if os.path.isdir(path) else open(path).read()
        return True
    except OSError:
        return False
def resolves(name):
    try:
        socket.getaddrinfo(name, 443)
        return True
    except OSError:
        return False
print("PROBE " + json.dumps({
    "uid": os.getuid(),
    "proxy": get(os.environ["SIA_LLM_URL"]),
    "host_service": get("http://host.docker.internal:%s" % os.environ["OTHER_PORT"]),
    "runtime_readable": readable("/var/lib/sia"),
    "owns_harness": os.stat(os.path.expanduser("~/harness/start.sh")).st_uid == os.getuid(),
    "can_write": all(os.access(d, os.W_OK) for d in ("/agent", "/env", "/workspace")),
    "dns": resolves("example.com"),
}))
"""


def _launch_probe(sb: DockerSandbox, proxy_port: int, other_port: int) -> dict:
    import json

    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp)
        (h / "probe.py").write_text(PROBE)
        (h / "start.sh").write_text('exec python3 "$HOME/harness/probe.py"\n')
        sb.upload_dir(h, sb.paths.harness, replace=True)
    sb.start_harness({"HOME": sb.paths.agent_home, "SIA_LLM_URL": sb.proxy_url(proxy_port), "OTHER_PORT": str(other_port)},
                     f"{sb.paths.runtime}/probe.log")
    deadline = time.time() + 60
    while sb.poll_harness() is None and time.time() < deadline:
        time.sleep(0.5)
    log = sb.read_text(f"{sb.paths.runtime}/probe.log") or ""
    line = next((l for l in log.splitlines() if l.startswith("PROBE ")), None)
    assert line, log
    return json.loads(line[len("PROBE "):])


def _sandbox(network: str, proxy_port: int) -> DockerSandbox:
    return DockerSandbox("sia-sandbox:latest", network=network, proxy_port=proxy_port)


def test_open_network_control(servers):
    proxy_port, other_port = servers
    sb = _sandbox("open", proxy_port)
    sb.setup()
    try:
        p = _launch_probe(sb, proxy_port, other_port)
    finally:
        sb.teardown()
    assert p["proxy"] == "proxy" and p["host_service"] == "other host service"


def test_agent_is_confined(servers):
    proxy_port, other_port = servers
    sb = _sandbox("proxy_only", proxy_port)
    sb.setup()
    try:
        p = _launch_probe(sb, proxy_port, other_port)
    finally:
        sb.teardown()
    assert p["uid"] != 0
    assert p["proxy"] == "proxy"  # the LLM proxy is reachable through the forwarder...
    assert p["host_service"].startswith("ERR")  # ...nothing else on the host is
    assert not p["dns"]
    assert not p["runtime_readable"]  # harness logs, exit codes, transfers
    assert p["owns_harness"] and p["can_write"]
    names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    assert not [n for n in names if n.startswith(sb.name)]
    nets = subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"], capture_output=True, text=True).stdout.split()
    assert sb.net_name not in nets


def test_hidden_tests_never_enter_the_agent_container(servers, tmp_path):
    proxy_port, other_port = servers
    task_dir = tmp_path / "t"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "prompt.md").write_text("# t\n")
    # The solution runs inside the grader: record what it can see, and fail loudly if it has network.
    (task_dir / "tests" / "test_t.py").write_text(
        "import socket\n"
        "from solution import answer\n"
        "def test_answer():\n    assert answer() == 42\n"
        "def test_grader_has_no_network():\n"
        f"    try:\n        socket.create_connection(('host.docker.internal', {other_port}), 2)\n"
        "    except OSError:\n        return\n    raise AssertionError('grader reached the host')\n"
    )
    task = tasks.Task("t", task_dir, "# t\n")
    sb = _sandbox("proxy_only", proxy_port)
    sb.setup()
    try:
        workdir = f"{sb.paths.workspace}/t"
        tasks.prepare_workdir(sb, task, workdir)
        sb.write_bytes(f"{workdir}/solution.py", b"def answer():\n    return 42\n")
        g = tasks.grade(sb, task, workdir)
        assert (g.passed, g.total, g.error) == (2, 2, None), g
        assert sb.exec("find / -xdev -name test_t.py 2>/dev/null")[1].strip() == ""
        names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
        assert not [n for n in names if "-grade-" in n and n.startswith(sb.name)]
    finally:
        sb.teardown()
