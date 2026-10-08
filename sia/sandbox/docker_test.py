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
        self.sb = DockerSandbox("img:1", name="sia-x")

    def argv(self, i=-1):
        return self.run.call_args_list[i][0][0]

    def test_defaults(self):
        self.assertTrue(DockerSandbox("img").name.startswith("sia-"))
        self.assertEqual(self.sb.paths.agent_home, "/agent")
        self.assertEqual(self.sb.proxy_url(8080), "http://host.docker.internal:8080")

    def test_setup_runs_container_and_makes_dirs(self):
        self.sb.setup()
        run_argv = self.argv(0)
        self.assertEqual(run_argv[:5], ["docker", "run", "-d", "--name", "sia-x"])
        self.assertIn("host.docker.internal:host-gateway", run_argv)
        self.assertEqual(run_argv[-3:], ["img:1", "sleep", "infinity"])
        self.assertEqual(self.argv(1)[:5], ["docker", "exec", "sia-x", "sh", "-c"])
        self.assertTrue(self.argv(1)[5].startswith("mkdir -p /agent"))

    def test_setup_failure(self):
        self.run.return_value = done(1, err=b"no such image")
        with self.assertRaisesRegex(RuntimeError, "no such image"):
            self.sb.setup()

    def test_teardown(self):
        self.sb.teardown()
        self.assertEqual(self.argv(), ["docker", "rm", "-f", "sia-x"])

    def test_exec(self):
        self.run.return_value = done(3, b"out", b"err")
        self.assertEqual(self.sb.exec("ls", timeout=7), (3, "outerr"))
        self.assertEqual(self.run.call_args.kwargs["timeout"], 7)
        self.run.side_effect = subprocess.TimeoutExpired("docker", 7)
        self.assertEqual(self.sb.exec("sleep 9"), (124, "timeout"))

    def test_write_bytes(self):
        self.sb.write_bytes("/env/a b", b"data")
        self.assertEqual(self.argv()[:4], ["docker", "exec", "-i", "sia-x"])
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


if __name__ == "__main__":
    unittest.main()
