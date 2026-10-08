import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HARNESS = str(Path(__file__).resolve().parent.parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

from tools import Context, bash  # noqa: E402


class BashToolTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(workdir=Path("/work"), task_id="t", cfg={})
        patcher = mock.patch.object(bash.subprocess, "run")
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def test_runs_in_workdir_with_timeout(self):
        self.run.return_value = subprocess.CompletedProcess([], 0, "hello\n", "")
        self.assertEqual(bash.run({"command": "echo hello", "timeout": 5}, self.ctx), "hello\n\n[exit code 0]")
        self.run.assert_called_once_with(["bash", "-c", "echo hello"], cwd=Path("/work"), capture_output=True, text=True, timeout=5)

    def test_default_timeout_and_stderr(self):
        self.run.return_value = subprocess.CompletedProcess([], 2, "", "oops")
        self.assertEqual(bash.run({"command": "false"}, self.ctx), "\n[stderr]\noops\n[exit code 2]")
        self.assertEqual(self.run.call_args.kwargs["timeout"], 120)

    def test_timeout(self):
        self.run.side_effect = subprocess.TimeoutExpired("bash", 1)
        self.assertEqual(bash.run({"command": "sleep 9"}, self.ctx), "[command timed out]")

    def test_output_truncated_to_config_limit(self):
        self.run.return_value = subprocess.CompletedProcess([], 0, "x" * 100, "")
        self.ctx.cfg["max_tool_output_chars"] = 20
        out = bash.run({"command": "yes"}, self.ctx)
        self.assertIn("characters omitted", out)
        self.assertTrue(out.endswith("code 0]"))

    def test_spec(self):
        self.assertEqual((bash.SPEC["name"], bash.SPEC["input_schema"]["required"]), ("bash", ["command"]))


if __name__ == "__main__":
    unittest.main()
