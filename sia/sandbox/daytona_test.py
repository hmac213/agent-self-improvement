import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from sia.config import REPO_ROOT
from sia.sandbox.daytona import DaytonaSandbox


def fake_daytona_module(remote):
    mod = types.ModuleType("daytona")
    mod.CreateSandboxFromImageParams = lambda **kw: SimpleNamespace(**kw)
    mod.Image = SimpleNamespace(from_dockerfile=lambda path: ("dockerfile", path))
    client = mock.Mock()
    client.create.return_value = remote
    mod.Daytona = mock.Mock(return_value=client)
    return mod, client


def fake_remote(uid="0"):
    remote = mock.Mock()
    remote.process.exec.side_effect = lambda cmd, timeout=None: SimpleNamespace(
        exit_code=0, result=uid + "\n" if cmd == "sh -c 'id -u'" else "ok")
    return remote


class DaytonaSandboxTest(unittest.TestCase):
    def setUp(self):
        self.remote = fake_remote()
        mod, self.client = fake_daytona_module(self.remote)
        patcher = mock.patch.dict(sys.modules, {"daytona": mod})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sb = DaytonaSandbox("img", "https://tunnel.example/", labels={"run": "r1"})

    def commands(self):
        return [c[0][0] for c in self.remote.process.exec.call_args_list]

    def test_requires_public_url_unless_offline(self):
        with self.assertRaisesRegex(ValueError, "proxy_public_url"):
            DaytonaSandbox("img", None)
        DaytonaSandbox("img", None, network="none")
        with self.assertRaisesRegex(ValueError, "unknown daytona network mode"):
            DaytonaSandbox("img", "https://t", network="bridge")

    def test_proxy_url_ignores_port(self):
        self.assertEqual(self.sb.proxy_url(1234), "https://tunnel.example")

    def test_network_params(self):
        self.assertEqual(self.sb.network_params(), {"domain_allow_list": "tunnel.example"})
        self.assertEqual(DaytonaSandbox("i", "http://203.0.113.7:8787").network_params(), {"network_allow_list": "203.0.113.7/32"})
        self.assertEqual(DaytonaSandbox("i", "https://t", "10.0.0.0/8").network_params(), {"network_allow_list": "10.0.0.0/8"})
        self.assertEqual(DaytonaSandbox("i", "https://t", network="open").network_params(), {"network_allow_list": None})
        self.assertEqual(DaytonaSandbox("i", None, network="none").network_params(), {"network_block_all": True})

    def test_setup_creates_root_sandbox_with_agent_user(self):
        self.sb.setup()
        params = self.client.create.call_args[0][0]
        self.assertEqual((params.image, params.labels, params.os_user, params.auto_stop_interval, params.domain_allow_list),
                         ("img", {"sia": "1", "run": "r1"}, "root", 0, "tunnel.example"))
        cmds = self.commands()
        self.assertEqual(cmds[0], "sh -c 'id -u'")
        self.assertIn("useradd --home-dir /agent --create-home --shell /bin/sh agent", cmds[1])
        self.assertIn("setpriv", cmds[1])
        self.assertIn("mkdir -p /agent /env /workspace /var/lib/sia", cmds[2])
        self.assertIn("chown -R agent:agent /agent /env /workspace && chmod 700 /var/lib/sia", cmds[3])

    def test_default_image_is_built_from_the_dockerfile(self):
        DaytonaSandbox("sia-sandbox:latest", "https://t").setup()
        self.assertEqual(self.client.create.call_args[0][0].image, ("dockerfile", REPO_ROOT / "sandbox_image" / "Dockerfile"))

    def test_setup_requires_root(self):
        self.remote.process.exec.side_effect = None
        self.remote.process.exec.return_value = SimpleNamespace(exit_code=0, result="1001\n")
        with self.assertRaisesRegex(RuntimeError, "must run as root"):
            self.sb.setup()

    def test_grading_sandbox(self):
        g = self.sb.grading_sandbox()
        self.assertEqual((g.image, g.network, g.agent_user, g.labels), ("img", "none", None, {"run": "r1", "role": "grader"}))
        g.setup()
        self.assertTrue(self.client.create.call_args[0][0].network_block_all)
        self.assertFalse(any("useradd" in c or "chown" in c for c in self.commands()))

    def test_exec_wraps_in_sh(self):
        self.sb.sb = self.remote
        self.remote.process.exec.side_effect = None
        self.remote.process.exec.return_value = SimpleNamespace(exit_code=2, result=None)
        self.assertEqual(self.sb.exec("echo 'x'", timeout=9.5), (2, ""))
        self.remote.process.exec.assert_called_once_with("sh -c 'echo '\"'\"'x'\"'\"''", timeout=9)

    def test_write_and_read_bytes(self):
        self.sb.sb = self.remote
        self.sb.write_bytes("/d/f.txt", b"hi")
        self.assertEqual(self.commands()[-1], "sh -c 'mkdir -p /d'")
        self.remote.fs.upload_file.assert_called_once_with(b"hi", "/d/f.txt")
        self.remote.fs.download_file.return_value = b"hi"
        self.assertEqual(self.sb.read_bytes("/d/f.txt"), b"hi")
        self.remote.process.exec.side_effect = None
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
