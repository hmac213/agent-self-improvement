import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sia import tasks
from sia.sandbox import SandboxPaths

JUNIT = b"""<?xml version="1.0"?>
<testsuites><testsuite>
  <testcase name="test_ok"/>
  <testcase name="test_fail"><failure message="assert 1 == 2"/></testcase>
  <testcase name="test_err"><error message="boom"/></testcase>
  <testcase name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""


def make_task(root: Path, task_id: str, prompt: str = "# Title\nbody", tests: str | None = None, starter: bool = False):
    d = root / task_id
    d.mkdir(parents=True)
    (d / "prompt.md").write_text(prompt)
    if tests is not None:
        (d / "tests").mkdir()
        (d / "tests" / "test_x.py").write_text(tests)
    if starter:
        (d / "starter").mkdir()
        (d / "starter" / "a.py").write_text("")
    return tasks.Task(id=task_id, dir=d, prompt=prompt)


def fake_sandbox():
    sb = mock.Mock()
    sb.paths = SandboxPaths(agent_home="/a", env_dir="/e", workspace="/w", runtime="/rt")
    sb.python = "py"
    sb.exec.return_value = (0, "pytest output")
    sb.grading_sandbox.return_value = None
    return sb


class TaskModelTest(unittest.TestCase):
    def test_title_and_score(self):
        self.assertEqual(tasks.Task("t", Path("."), "\n# My task \nmore").title, "My task")
        self.assertEqual(tasks.Grade("t", 3, 4).score, 0.75)
        self.assertEqual(tasks.Grade("t", 0, 0).score, 0.0)


class SuiteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_suite_sorted_without_meta(self):
        for i in ("b", "a"):
            make_task(self.root, i)
        (self.root / "not_a_task").mkdir()
        self.assertEqual([t.id for t in tasks.load_suite(self.root)], ["a", "b"])

    def test_load_suite_explicit_order(self):
        for i in ("a", "b"):
            make_task(self.root, i)
        (self.root / "suite.toml").write_text('name = "s"\norder = ["b", "a"]\n')
        suite = tasks.load_suite(self.root)
        self.assertEqual([t.id for t in suite], ["b", "a"])
        self.assertEqual(suite[0].prompt, "# Title\nbody")

    def test_schedule(self):
        ts = [tasks.Task(i, Path(i), "p") for i in "abc"]
        self.assertEqual([i for i, _ in tasks.schedule(ts, "fixed", 1, 0)], ["a", "b", "c"])
        ids = [i for i, _ in tasks.schedule(ts, "fixed", 2, 0)]
        self.assertEqual(ids, ["a-e0", "b-e0", "c-e0", "a-e1", "b-e1", "c-e1"])
        shuffled = [t.id for _, t in tasks.schedule(ts, "shuffled", 3, 1)]
        self.assertEqual(sorted(shuffled), sorted("abc" * 3))
        self.assertEqual(shuffled, [t.id for _, t in tasks.schedule(ts, "shuffled", 3, 1)])  # deterministic

    def test_prepare_workdir(self):
        sb = fake_sandbox()
        tasks.prepare_workdir(sb, make_task(self.root, "plain"), "/w/plain")
        sb.check.assert_called_once_with("mkdir -p /w/plain")
        sb.upload_dir.assert_not_called()
        t = make_task(self.root, "with_starter", starter=True)
        tasks.prepare_workdir(sb, t, "/w/s")
        sb.upload_dir.assert_called_once_with(t.dir / "starter", "/w/s")
        self.assertEqual(sb.give_to_agent.call_args_list, [mock.call("/w/plain"), mock.call("/w/s")])

    def test_count_tests(self):
        t = make_task(self.root, "t", tests="def test_a():\n    pass\n\n  def test_b(): pass\ndef helper(): pass\n")
        self.assertEqual(tasks._count_tests(t), 2)
        empty = make_task(self.root, "e", tests="")
        self.assertEqual(tasks._count_tests(empty), 1)

    def test_parse_junit(self):
        g = tasks._parse_junit("t", JUNIT, expected=2)
        self.assertEqual((g.passed, g.total, g.error), (1, 4, None))
        self.assertEqual([f["test"] for f in g.failures], ["test_fail", "test_err"])
        self.assertEqual(g.failures[0]["message"], "assert 1 == 2")
        g = tasks._parse_junit("t", b"<testsuites/>", expected=5)
        self.assertEqual((g.passed, g.total, g.error), (0, 5, "no tests collected"))

    def test_grade_with_report(self):
        t = make_task(self.root, "t", tests="def test_a(): pass\n")
        sb = fake_sandbox()
        sb.read_bytes.return_value = JUNIT
        g = tasks.grade(sb, t, "/w/t", timeout=10)
        self.assertEqual((g.task_id, g.passed, g.total), ("t", 1, 4))
        scratch = sb.upload_dir.call_args[0][1].rsplit("/", 1)[0]
        self.assertTrue(scratch.startswith("/rt/grade-"))
        sb.upload_dir.assert_called_once_with(t.dir / "tests", f"{scratch}/_hidden_tests")
        cmd = sb.exec.call_args_list[0][0][0]
        self.assertIn("timeout 10 py -m pytest", cmd)
        self.assertEqual(sb.exec.call_args_list[-1][0][0], f"rm -rf {scratch}")  # always cleaned up

    def test_grade_without_report(self):
        t = make_task(self.root, "t", tests="def test_a(): pass\ndef test_b(): pass\n")
        sb = fake_sandbox()
        sb.read_bytes.return_value = None
        g = tasks.grade(sb, t, "/w/t")
        self.assertEqual((g.passed, g.total), (0, 2))
        self.assertIn("pytest output", g.error)

    def test_grade_in_grading_sandbox(self):
        t = make_task(self.root, "t", tests="def test_a(): pass\n")
        sb, grader = fake_sandbox(), fake_sandbox()
        sb.grading_sandbox.return_value = grader
        sb.pack_dir.return_value = b"tgz"
        grader.read_bytes.return_value = JUNIT
        g = tasks.grade(sb, t, "/w/t")
        self.assertEqual((g.passed, g.total), (1, 4))
        sb.pack_dir.assert_called_once_with("/w/t")
        grader.setup.assert_called_once()
        grader.unpack_dir.assert_called_once_with(b"tgz", "/w/t")
        # The hidden tests only ever reach the grader.
        grader.upload_dir.assert_called_once()
        sb.upload_dir.assert_not_called()
        sb.exec.assert_not_called()
        grader.teardown.assert_called_once()

    def test_grading_sandbox_torn_down_on_error(self):
        t = make_task(self.root, "t", tests="")
        sb, grader = fake_sandbox(), fake_sandbox()
        sb.grading_sandbox.return_value = grader
        sb.pack_dir.return_value = None  # the agent deleted its working directory
        grader.check.side_effect = RuntimeError("fail")
        with self.assertRaises(RuntimeError):
            tasks.grade(sb, t, "/w/t")
        self.assertEqual(grader.check.call_args_list[0], mock.call("mkdir -p /w/t"))
        grader.teardown.assert_called_once()

    def test_grade_cleans_up_on_error(self):
        t = make_task(self.root, "t", tests="")
        sb = fake_sandbox()
        sb.check.side_effect = RuntimeError("fail")
        with self.assertRaises(RuntimeError):
            tasks.grade(sb, t, "/w/t")
        self.assertTrue(sb.exec.call_args[0][0].startswith("rm -rf /rt/grade-"))


if __name__ == "__main__":
    unittest.main()
