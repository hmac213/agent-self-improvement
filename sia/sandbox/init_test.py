"""Tests for sia/sandbox/__init__.py (make_sandbox)."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sia.config import SandboxCfg
from sia.sandbox import make_sandbox
from sia.sandbox.daytona import DaytonaSandbox
from sia.sandbox.docker import DockerSandbox
from sia.sandbox.local import LocalSandbox


class MakeSandboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_local(self):
        sb = make_sandbox(SandboxCfg(kind="local"), self.run_dir, "r1")
        self.assertIsInstance(sb, LocalSandbox)
        self.assertEqual(sb.root, (self.run_dir / "sandbox").resolve())

    def test_docker(self):
        sb = make_sandbox(SandboxCfg(kind="docker", image="img"), self.run_dir, "r1")
        self.assertIsInstance(sb, DockerSandbox)
        self.assertEqual((sb.image, sb.name), ("img", "sia-r1"))

    def test_daytona(self):
        cfg = SandboxCfg(kind="daytona", image="img", proxy_public_url="https://t", daytona_network_allow_list="1.2.3.4/32")
        sb = make_sandbox(cfg, self.run_dir, "r1")
        self.assertIsInstance(sb, DaytonaSandbox)
        self.assertEqual((sb.public_url, sb.network_allow_list, sb.labels), ("https://t", "1.2.3.4/32", {"run": "r1"}))

    def test_backends_are_not_started(self):
        with mock.patch("subprocess.run") as run:
            make_sandbox(SandboxCfg(kind="docker"), self.run_dir, "r1")
        run.assert_not_called()

    def test_unknown(self):
        with self.assertRaisesRegex(ValueError, "unknown sandbox kind 'vm'"):
            make_sandbox(SandboxCfg(kind="vm"), self.run_dir, "r1")


if __name__ == "__main__":
    unittest.main()
