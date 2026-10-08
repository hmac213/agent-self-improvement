import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sia.sandbox import local
from sia.sandbox.local import LocalSandbox


class LocalSandboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "sb"
        self.sb = LocalSandbox(self.root)
        run = mock.patch.object(local.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "out", "err"))
        self.run = run.start()
        self.addCleanup(run.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_paths_under_root(self):
        p = self.sb.paths
        self.assertEqual((p.agent_home, p.env_dir, p.workspace, p.runtime),
                         tuple(str(self.root.resolve() / d) for d in ("agent", "env", "workspace", "runtime")))
        self.assertEqual(self.sb.python, sys.executable)
        self.assertEqual(self.sb.proxy_url(99), "http://127.0.0.1:99")

    def test_exec(self):
        self.assertEqual(self.sb.exec("echo hi", timeout=5), (0, "outerr"))
        self.run.assert_called_once_with(["sh", "-c", "echo hi"], capture_output=True, text=True, timeout=5)
        self.run.side_effect = subprocess.TimeoutExpired("sh", 5)
        self.assertEqual(self.sb.exec("sleep 9"), (124, "timeout"))

    def test_setup_and_teardown(self):
        self.sb.setup()
        self.assertTrue(self.root.is_dir())
        self.assertTrue(self.run.call_args[0][0][2].startswith("mkdir -p "))
        self.sb.teardown()
        self.assertIn("kill -TERM", self.run.call_args[0][0][2])

    def test_write_and_read_bytes(self):
        path = str(self.root / "deep" / "f.bin")
        self.sb.write_bytes(path, b"abc")
        self.assertEqual(self.sb.read_bytes(path), b"abc")
        self.assertIsNone(self.sb.read_bytes(str(self.root / "missing")))
        self.assertIsNone(self.sb.read_bytes(str(self.root / "deep")))  # a directory

    def test_destroy(self):
        self.sb.write_bytes(str(self.root / "f"), b"x")
        self.sb.destroy()
        self.assertFalse(self.root.exists())
        self.sb.destroy()  # idempotent


if __name__ == "__main__":
    unittest.main()
