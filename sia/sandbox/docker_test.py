import subprocess
import unittest
from unittest import mock

from sia.sandbox import docker
from sia.sandbox.docker import DockerSandbox


def done(code=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], code, out, err)


class DockerSandboxTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(docker.subprocess, "run", return_value=done())
        self.run = patcher.start()
        self.addCleanup(patcher.stop)
        self.sb = DockerSandbox("img:1", name="sia-x", proxy_port=4321)

    def argv(self, i=-1):
        return self.run.call_args_list[i][0][0]

    def argvs(self):
        return [c[0][0] for c in self.run.call_args_list]

    def test_defaults(self):
        self.assertTrue(DockerSandbox("img").name.startswith("sia-"))
        self.assertEqual((self.sb.paths.agent_home, self.sb.agent_user, self.sb.network), ("/agent", "agent", "proxy_only"))
        self.assertEqual(self.sb.proxy_url(4321), "http://sia-proxy:8080")
        self.assertEqual(DockerSandbox("img", network="open").proxy_url(8080), "http://host.docker.internal:8080")
        with self.assertRaisesRegex(ValueError, "unknown docker network mode"):
            DockerSandbox("img", network="bridge")

    def test_setup_proxy_only(self):
        self.sb.setup()
        calls = self.argvs()
        self.assertEqual(calls[0], ["docker", "network", "create", "--internal", "sia-x-net"])
        fwd = calls[1]
        self.assertEqual(fwd[:5], ["docker", "run", "-d", "--name", "sia-x-proxy"])
        self.assertIn("host.docker.internal:host-gateway", fwd)
        self.assertEqual(fwd[-3:], ["host.docker.internal", "4321", "8080"])
        self.assertEqual(calls[2], ["docker", "network", "connect", "--alias", "sia-proxy", "sia-x-net", "sia-x-proxy"])
        run = calls[3]
        self.assertEqual(run[:5], ["docker", "run", "-d", "--name", "sia-x"])
        self.assertEqual(run[run.index("--network") + 1], "sia-x-net")
        self.assertNotIn("host.docker.internal:host-gateway", run)
        self.assertIn("no-new-privileges", run)
        self.assertEqual(run[-3:], ["img:1", "sleep", "infinity"])
        execs = [c[-1] for c in calls[4:]]
        self.assertEqual(execs[0], "id -u agent")
        self.assertTrue(execs[1].startswith("mkdir -p /agent"))
        self.assertIn("chown -R agent:agent /agent /env /workspace && chmod 700 /var/lib/sia", execs[2])
        self.assertIn("('sia-proxy', 8080)", execs[3])  # waits until the forwarder answers

    def test_setup_open_and_none(self):
        DockerSandbox("img", name="o", network="open").setup()
        run = self.argv(0)
        self.assertIn("host.docker.internal:host-gateway", run)
        self.assertNotIn("--network", run)
        self.run.reset_mock()
        DockerSandbox("img", name="n", network="none", agent_user=None).setup()
        run = self.argv(0)
        self.assertEqual(run[run.index("--network") + 1], "none")
        self.assertNotIn("SETUID", run)
        self.assertFalse(any("chown" in c[-1] for c in self.argvs()))

    def test_setup_failures(self):
        with self.assertRaisesRegex(ValueError, "port"):
            DockerSandbox("img", name="p").setup()
        self.run.return_value = done(1, err=b"no such image")
        with self.assertRaisesRegex(RuntimeError, "no such image"):
            DockerSandbox("img", name="o", network="open").setup()

    def test_setup_requires_agent_user(self):
        self.run.side_effect = lambda argv, **kw: done(1 if argv[-1] == "id -u agent" else 0)
        with self.assertRaisesRegex(RuntimeError, "no user 'agent'"):
            DockerSandbox("img", name="o", network="open").setup()

    def test_teardown(self):
        self.sb.teardown()
        self.assertEqual(self.argvs(), [["docker", "rm", "-f", "sia-x"], ["docker", "rm", "-f", "sia-x-proxy"],
                                        ["docker", "network", "rm", "sia-x-net"]])

    def test_exec_runs_as_root(self):
        self.run.return_value = done(3, b"out", b"err")
        self.assertEqual(self.sb.exec("ls", timeout=7), (3, "outerr"))
        self.assertEqual(self.argv()[:6], ["docker", "exec", "-u", "0", "sia-x", "sh"])
        self.assertEqual(self.run.call_args.kwargs["timeout"], 7)
        self.run.side_effect = subprocess.TimeoutExpired("docker", 7)
        self.assertEqual(self.sb.exec("sleep 9"), (124, "timeout"))

    def test_write_bytes(self):
        self.sb.write_bytes("/env/a b", b"data")
        self.assertEqual(self.argv()[:6], ["docker", "exec", "-i", "-u", "0", "sia-x"])
        self.assertEqual(self.argv()[-2:], ["_", "/env/a b"])
        self.assertEqual(self.run.call_args.kwargs["input"], b"data")
        self.run.return_value = done(1, err=b"denied")
        with self.assertRaisesRegex(RuntimeError, "denied"):
            self.sb.write_bytes("/x", b"")

    def test_read_bytes(self):
        self.run.return_value = done(0, b"content")
        self.assertEqual(self.sb.read_bytes("/f"), b"content")
        self.assertEqual(self.argv()[-1], "/f")
        self.run.return_value = done(1)
        self.assertIsNone(self.sb.read_bytes("/missing"))

    def test_grading_sandbox(self):
        g = self.sb.grading_sandbox()
        self.assertTrue(g.name.startswith("sia-x-grade-"))
        self.assertEqual((g.image, g.network, g.agent_user), ("img:1", "none", None))
        self.assertNotEqual(g.name, self.sb.grading_sandbox().name)


if __name__ == "__main__":
    unittest.main()
