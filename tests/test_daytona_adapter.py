"""Runs the Daytona backend against a fake `daytona` module that executes
locally. Checks the adapter's own logic; it does not touch Daytona's cloud."""

import socket
import subprocess
import sys
import types
from pathlib import Path

from sia import report
from sia.config import REPO_ROOT, load
from sia.supervisor import Supervisor


class _FakeSandbox:
    def __init__(self, home: Path):
        self.home = home
        self.deleted = False

        class Process:
            def exec(self, command, timeout=None):
                # The real SDK runs the command string; ours is always `sh -c '<quoted>'`.
                p = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
                return types.SimpleNamespace(exit_code=p.returncode, result=p.stdout + p.stderr)

        class FS:
            def upload_file(self, src, dst, timeout=1800):
                Path(dst).write_bytes(src)

            def download_file(self, path):
                return Path(path).read_bytes()

        self.process, self.fs = Process(), FS()

    def get_user_home_dir(self):
        return str(self.home)

    def delete(self):
        self.deleted = True


def _install_fake_daytona(monkeypatch, home: Path):
    created = []

    class Daytona:
        def create(self, params, timeout=60):
            created.append(params)
            sb = _FakeSandbox(home)
            created.append(sb)
            return sb

    mod = types.ModuleType("daytona")
    mod.Daytona = Daytona
    mod.CreateSandboxFromImageParams = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "daytona", mod)
    return created


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_daytona_backend_with_fake_sdk(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    created = _install_fake_daytona(monkeypatch, home)
    port = _free_port()
    cfg = load(REPO_ROOT / "experiments" / "mock_smoke.toml", {
        "sandbox.kind": "daytona",
        "sandbox.proxy_port": port,
        "sandbox.proxy_public_url": f"http://127.0.0.1:{port}",
    })
    run_dir = tmp_path / "run"
    summary = Supervisor(cfg, run_dir, verbose=False).run()
    a = report.analyze(run_dir)
    assert summary["mean_score"] == 1.0 and a["signals"]["harness_modified"]
    assert (home / "agent" / "harness" / "tools" / "note.py").exists()
    assert created[1].deleted
