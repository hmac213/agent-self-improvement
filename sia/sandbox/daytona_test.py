import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from sia.sandbox.daytona import DaytonaSandbox


def fake_daytona_module(remote):
    mod = types.ModuleType("daytona")
    mod.CreateSandboxFromImageParams = lambda **kw: SimpleNamespace(**kw)
    client = mock.Mock()
    client.create.return_value = remote
    mod.Daytona = mock.Mock(return_value=client)
    return mod, client


def fake_remote():
    remote = mock.Mock()
    remote.get_user_home_dir.return_value = "/home/daytona/"
    remote.process.exec.return_value = SimpleNamespace(exit_code=0, result="ok")
    return remote


class DaytonaSandboxTest(unittest.TestCase):
    def setUp(self):
        self.remote = fake_remote()
        mod, self.client = fake_daytona_module(self.remote)
        patcher = mock.patch.dict(sys.modules, {"daytona": mod})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sb = DaytonaSandbox("img", "https://tunnel.example/", "10.0.0.0/8", labels={"run": "r1"})

    def test_requires_public_url(self):
        with self.assertRaisesRegex(ValueError, "proxy_public_url"):
            DaytonaSandbox("img", None)

    def test_proxy_url_ignores_port(self):
        self.assertEqual(self.sb.proxy_url(1234), "https://tunnel.example")

    def test_setup_creates_sandbox_and_dirs(self):
        self.sb.setup()
        params = self.client.create.call_args[0][0]
        self.assertEqual((params.image, params.labels, params.auto_stop_interval, params.network_allow_list),
                         ("img", {"sia": "1", "run": "r1"}, 0, "10.0.0.0/8"))
        p = self.sb.paths
        self.assertEqual((p.agent_home, p.env_dir, p.workspace, p.runtime),
                         ("/home/daytona/agent", "/home/daytona/env", "/home/daytona/workspace", "/home/daytona/.sia"))
        self.assertIn("mkdir -p /home/daytona/agent", self.remote.process.exec.call_args[0][0])

    def test_exec_wraps_in_sh(self):
        self.sb.sb = self.remote
        self.remote.process.exec.return_value = SimpleNamespace(exit_code=2, result=None)
        self.assertEqual(self.sb.exec("echo 'x'", timeout=9.5), (2, ""))
        self.remote.process.exec.assert_called_once_with("sh -c 'echo '\"'\"'x'\"'\"''", timeout=9)

    def test_write_and_read_bytes(self):
        self.sb.sb = self.remote
        self.sb.write_bytes("/d/f.txt", b"hi")
        self.assertEqual(self.remote.process.exec.call_args[0][0], "sh -c 'mkdir -p /d'")
        self.remote.fs.upload_file.assert_called_once_with(b"hi", "/d/f.txt")
        self.remote.fs.download_file.return_value = b"hi"
        self.assertEqual(self.sb.read_bytes("/d/f.txt"), b"hi")
        self.remote.process.exec.return_value = SimpleNamespace(exit_code=1, result="")
        self.assertIsNone(self.sb.read_bytes("/missing"))

    def test_teardown_deletes_once(self):
        self.sb.teardown()  # nothing created yet
        self.sb.sb = self.remote
        self.sb.teardown()
        self.sb.teardown()
        self.remote.delete.assert_called_once()
        self.assertIsNone(self.sb.sb)


if __name__ == "__main__":
    unittest.main()
