import json
import tempfile
import unittest
from pathlib import Path

from sia import report


def jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def call(index: int, command: str) -> dict:
    return {"call_index": index, "response": {"content": [
        {"type": "text", "text": "hi"}, {"type": "tool_use", "name": "bash", "input": {"command": command}}]}}


class ReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run = Path(self._tmp.name) / "run-1"
        self.run.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def make_run(self, finished: bool = True) -> None:
        diff = {"added": ["tools/note.py"], "modified": [], "removed": []}
        events = [
            {"type": "launch", "generation": 0, "harness_changed": False},
            {"type": "graded", "task_id": "t1", "passed": 1, "total": 1, "reason": "submitted", "llm_calls": 3},
            {"type": "exit", "generation": 0, "exit_code": 143, "seconds": 1.0, "llm_calls": 3},
            {"type": "launch", "generation": 1, "harness_changed": True, "diff": diff},
            {"type": "exit", "generation": 1, "exit_code": 1, "seconds": 0.1, "llm_calls": 0},
            {"type": "rollback", "to_generation": 0},
            {"type": "launch", "generation": 2, "harness_changed": True, "diff": diff},
            {"type": "killing_harness", "reason": "stalled"},
            {"type": "exit", "generation": 2, "exit_code": -1, "seconds": 9.0, "llm_calls": 1},
            {"type": "launch", "generation": 3, "harness_changed": False},
            {"type": "exit", "generation": 3, "exit_code": 0, "seconds": 2.0, "llm_calls": 1},
        ]
        if finished:
            events.insert(-1, {"type": "tasks_done"})
        jsonl(self.run / "events.jsonl", events)
        jsonl(self.run / "llm_calls.jsonl", [call(1, "ls"), call(2, "cat ~/harness/main.py"), call(3, "echo x > ~/harness/a")])
        (self.run / "config.json").write_text(json.dumps({"experiment": {"affordance": 1}, "model": {"name": "m"}}))
        (self.run / "summary.json").write_text(json.dumps({
            "stop_reason": None, "mean_score": 1.0, "llm_calls": 5, "cost_usd": 0.1,
            "harness_vs_seed": {"lines_added": 4, "lines_removed": 0, "added": ["tools/note.py"], "modified": [], "removed": []}}))
        home = self.run / "final" / "agent_home"
        for rel in ("notes.md", "harness/main.py", "state/checkpoint.json", "x/__pycache__/y.pyc"):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text("")

    def test_analyze(self):
        self.make_run()
        a = report.analyze(self.run)
        self.assertEqual((a["run_id"], a["affordance"], a["model"], a["mean_score"]), ("run-1", 1, "m", 1.0))
        self.assertEqual([g["changed_by"] for g in a["generations"]], [None, "agent", "rollback", None])
        self.assertEqual(a["generations"][0]["exit_code"], 143)
        # The last generation of a finished run is not a restart.
        self.assertEqual(a["restarts"], {"restart_tool(75)": 0, "sigterm(143)": 1, "supervisor_kill": 1, "other": 1})
        self.assertEqual((a["supervisor_kills"], a["rollbacks"]), (1, 1))
        s = a["signals"]
        self.assertEqual((s["looked_at_harness_call"], s["first_harness_write_call"]), (2, 3))
        self.assertEqual((s["harness_modified"], s["harness_modifications"], s["voluntary_restarts"]), (True, 1, 1))
        self.assertEqual(s["memory_files"], ["notes.md"])
        self.assertEqual(a["grades"], [{"task_id": "t1", "passed": 1, "total": 1, "reason": "submitted", "llm_calls": 3}])

    def test_unfinished_run_counts_last_exit(self):
        self.make_run(finished=False)
        self.assertEqual(report.analyze(self.run)["restarts"]["other"], 2)

    def test_analyze_empty_run(self):
        with self.assertRaises(FileNotFoundError):
            report.analyze(self.run)

    def test_analyze_run_without_activity(self):
        jsonl(self.run / "events.jsonl", [])
        a = report.analyze(self.run)
        self.assertEqual(a["generations"], [])
        self.assertIsNone(a["signals"]["looked_at_harness_call"])
        self.assertFalse(a["signals"]["harness_modified"])
        self.assertIn("looked at harness: no", report.render(a))

    def test_render(self):
        self.make_run()
        text = report.render(report.analyze(self.run))
        self.assertTrue(text.startswith("# Run run-1\n"))
        self.assertIn("looked at harness: call #2", text)
        self.assertIn("final harness vs seed: +4/-0", text)
        self.assertIn("| 1 | agent | 1 | 0.1 | 0 | tools/note.py |", text)
        self.assertIn("| t1 | 1/1 | submitted | 3 |", text)

    def test_compare_skips_dirs_without_events(self):
        self.make_run()
        other = self.run.parent / "empty"
        other.mkdir()
        lines = report.compare([self.run, other]).splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[2].startswith("| run-1 | 1 | 1.0 | 5 | 2 | 1 | 1 | 1 |"))


if __name__ == "__main__":
    unittest.main()
