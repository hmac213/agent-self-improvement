"""Unit tests for sia/trajectories.py (run: python -m unittest sia.trajectories_test)."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from sia import trajectories
from sia.trajectories import TrajectoryDB

CFG = {"experiment": {"name": "exp", "affordance": 2}, "model": {"name": "m", "upstream": "mock"}, "sandbox": {"kind": "local"}}


def call(idx, gen=0, task="t1", tool_uses=(), results=(), usage=None):
    """A proxy log record shaped like LLMProxy._log's."""
    messages = [{"role": "user", "content": [{"type": "text", "text": "do it"}]}]
    if results:
        messages += [
            {"role": "assistant", "content": [{"type": "tool_use", "id": i, "name": "bash", "input": {}} for i, _ in results]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": i, "content": out, "is_error": err}
                                         for i, (out, err) in results]},
        ]
    content = [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in tool_uses] or [{"type": "text", "text": "ok"}]
    return {
        "ts": 100.0 + idx, "generation": gen, "task_id": task, "path": "/v1/generate", "requested_model": "m",
        "request": {"model": "m", "messages": messages}, "call_index": idx, "status": 200,
        "response": {"model": "m", "content": content, "stop_reason": "tool_use" if tool_uses else "end_turn"},
        "usage": usage or {"input_tokens": 10, "output_tokens": 5}, "cost_usd": 0.01, "latency_s": 0.1,
    }


class SchemaTest(unittest.TestCase):
    def test_file_db_uses_wal_and_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "t.db"
            with TrajectoryDB(path) as db:
                self.assertEqual(db.query("PRAGMA journal_mode")[0]["journal_mode"], "wal")
                self.assertEqual(db.query("PRAGMA user_version")[0]["user_version"], trajectories.SCHEMA_VERSION)
                tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"runs", "events", "generations", "tasks", "llm_calls", "tool_calls"} <= tables)
            TrajectoryDB(path).close()  # reopening is idempotent

    def test_readonly_rejects_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            TrajectoryDB(path).close()
            with TrajectoryDB(path, readonly=True) as db:
                with self.assertRaises(sqlite3.OperationalError):
                    db.start_run("r", CFG)


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.db = TrajectoryDB()
        self.db.start_run("r", CFG, "/runs/r", seed_harness="seed", inherited=False, started_at=1.0)

    def tearDown(self):
        self.db.close()

    def test_run_lifecycle(self):
        self.db.finish_run("r", {"stop_reason": "max_generations", "tasks_graded": 2, "mean_score": 0.5,
                                 "llm_calls": 7, "cost_usd": 0.2, "seconds": 3.0}, ended_at=5.0)
        r = self.db.run("r")
        self.assertEqual((r["experiment"], r["affordance"], r["model"], r["sandbox"]), ("exp", 2, "m", "local"))
        self.assertEqual(r["config"], CFG)
        self.assertEqual((r["stop_reason"], r["mean_score"], r["ended_at"]), ("max_generations", 0.5, 5.0))
        self.assertEqual(r["summary"]["llm_calls"], 7)
        self.assertEqual([x["run_id"] for x in self.db.runs()], ["r"])
        self.assertIsNone(self.db.run("missing"))

    def test_events_drive_generations_and_rollback_attribution(self):
        ev = lambda type, **d: self.db.record_event("r", {"ts": 1.0, "type": type, **d})  # noqa: E731
        ev("launch", generation=0, harness_hash="a", harness_changed=False, diff=None)
        ev("exit", generation=0, exit_code=143, seconds=1.5, llm_calls=3)
        ev("launch", generation=1, harness_hash="b", harness_changed=True, diff={"modified": ["main.py"]})
        ev("exit", generation=1, exit_code=1, seconds=0.1, llm_calls=0)
        ev("rollback", to_generation=0, failures=2)
        ev("launch", generation=2, harness_hash="a", harness_changed=True, diff={"modified": ["main.py"]})
        gens = self.db.generations("r")
        self.assertEqual([g["changed_by"] for g in gens], [None, "agent", "rollback"])
        self.assertEqual((gens[0]["exit_code"], gens[0]["llm_calls"]), (143, 3))
        self.assertEqual(gens[1]["diff"], {"modified": ["main.py"]})
        self.assertIsNone(gens[2]["exited_at"])
        self.assertEqual([e["type"] for e in self.db.events("r")], ["launch", "exit", "launch", "exit", "rollback", "launch"])
        self.assertEqual(self.db.events("r", "rollback")[0]["to_generation"], 0)

    def test_events_drive_tasks(self):
        self.db.record_event("r", {"ts": 1.0, "type": "task_posted", "task_id": "t1", "seq": 1, "prompt": "P1"})
        self.db.record_event("r", {"ts": 2.0, "type": "task_posted", "task_id": "t2", "seq": 2, "prompt": "P2"})
        self.db.record_event("r", {"ts": 3.0, "type": "graded", "task_id": "t1", "reason": "submitted", "passed": 2,
                                   "total": 3, "score": 0.667, "llm_calls": 4, "seconds": 2.0,
                                   "failures": [{"test": "x", "message": "y"}], "error": None})
        t1, t2 = self.db.tasks("r")
        self.assertEqual((t1["prompt"], t1["passed"], t1["total"], t1["reason"]), ("P1", 2, 3, "submitted"))
        self.assertEqual(t1["failures"], [{"test": "x", "message": "y"}])
        self.assertIsNone(t2["graded_at"])

    def test_llm_calls_and_tool_results_are_joined(self):
        self.db.record_llm_call("r", call(1, tool_uses=[("u1", "bash", {"command": "ls"}), ("u2", "submit", {})],
                                          usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 7}))
        self.db.record_llm_call("r", call(2, results=[("u1", ("file.txt", False)), ("u2", ([{"type": "text", "text": "x"}], True))]))
        calls = self.db.llm_calls("r")
        self.assertEqual([c["call_index"] for c in calls], [1, 2])
        self.assertNotIn("request_json", calls[0])
        self.assertEqual((calls[0]["input_tokens"], calls[0]["cache_read_tokens"], calls[0]["stop_reason"]), (10, 7, "tool_use"))
        full = self.db.llm_calls("r", payload=True)
        self.assertEqual(full[0]["response"]["content"][0]["id"], "u1")
        tools = self.db.tool_calls("r")
        self.assertEqual([(t["name"], t["output"], t["is_error"]) for t in tools],
                         [("bash", "file.txt", False), ("submit", json.dumps([{"type": "text", "text": "x"}]), True)])
        self.assertEqual(tools[0]["input"], {"command": "ls"})
        self.assertEqual([t["tool_use_id"] for t in self.db.tool_calls("r", name="submit")], ["u2"])

    def test_record_without_call_index_is_ignored(self):
        rec = call(1)
        rec.pop("call_index")
        self.db.record_llm_call("r", rec)
        self.assertEqual(self.db.llm_calls("r"), [])

    def test_restarting_a_run_replaces_its_rows(self):
        self.db.record_llm_call("r", call(1))
        self.db.record_event("r", {"ts": 1.0, "type": "notice", "text": "hi"})
        self.db.start_run("other", CFG)
        self.db.record_llm_call("other", call(1))
        self.db.start_run("r", CFG)
        self.assertEqual(self.db.llm_calls("r"), [])
        self.assertEqual(self.db.events("r"), [])
        self.assertEqual(len(self.db.llm_calls("other")), 1)

    def test_recorder_drops_records_after_close(self):
        rec = self.db.recorder("r")
        rec(call(1))
        self.assertEqual(len(self.db.llm_calls("r")), 1)
        self.db.close()
        rec(call(2))  # must not raise
        self.db = TrajectoryDB()


class ConcurrencyTest(unittest.TestCase):
    def test_threads_and_connections_write_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.db"
            dbs = [TrajectoryDB(path), TrajectoryDB(path)]  # e.g. two parallel runs sharing one file
            for i, db in enumerate(dbs):
                db.start_run(f"r{i}", CFG)
            errors = []

            def work(db, run_id, offset):
                try:
                    for k in range(50):
                        db.record_llm_call(run_id, call(offset + k, tool_uses=[(f"{run_id}-{offset + k}", "bash", {})]))
                        db.record_event(run_id, {"ts": 1.0, "type": "notice", "text": str(k)})
                except Exception as e:  # pragma: no cover - surfaced below
                    errors.append(e)

            threads = [threading.Thread(target=work, args=(dbs[i % 2], f"r{i % 2}", 1 + 100 * (i // 2))) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            for db in dbs:
                db.close()
            with TrajectoryDB(path, readonly=True) as db:
                for run_id in ("r0", "r1"):
                    self.assertEqual(len(db.llm_calls(run_id)), 100)
                    self.assertEqual(len(db.tool_calls(run_id)), 100)
                    self.assertEqual(len(db.events(run_id)), 100)


class PathsAndImportTest(unittest.TestCase):
    def test_default_path(self):
        root = Path("/runs")
        self.assertEqual(trajectories.default_path(root), root / "trajectories.db")
        self.assertEqual(trajectories.default_path(root, "x/y.db"), root / "x/y.db")
        self.assertEqual(trajectories.default_path(root, "/abs/y.db"), Path("/abs/y.db"))

    def _legacy_run(self, root: Path) -> Path:
        run = root / "old-run"
        run.mkdir()
        (run / "config.json").write_text(json.dumps(CFG))
        events = [
            {"ts": 1.0, "type": "run_start", "seed": "s", "inherited": False},
            {"ts": 2.0, "type": "task_posted", "task_id": "t1", "seq": 1, "prompt": "P"},
            {"ts": 3.0, "type": "launch", "generation": 0, "harness_hash": "a", "harness_changed": False, "diff": None},
            {"ts": 4.0, "type": "graded", "task_id": "t1", "reason": "submitted", "passed": 1, "total": 1, "score": 1.0, "llm_calls": 1},
            {"ts": 5.0, "type": "run_end", "mean_score": 1.0},
        ]
        (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        (run / "llm_calls.jsonl").write_text(json.dumps(call(1, tool_uses=[("u1", "bash", {"command": "cat ~/harness/main.py"})])) + "\n")
        (run / "summary.json").write_text(json.dumps({"mean_score": 1.0, "tasks_graded": 1, "llm_calls": 1}))
        return run

    def test_open_for_run_imports_legacy_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._legacy_run(Path(tmp))
            db = trajectories.open_for_run(run)
            with db:
                self.assertEqual(db.path, ":memory:")
                self.assertEqual(db.run("old-run")["mean_score"], 1.0)
                self.assertEqual(db.tasks("old-run")[0]["passed"], 1)
                self.assertEqual(db.tool_calls("old-run")[0]["call_index"], 1)
            self.assertIsNone(trajectories.open_for_run(Path(tmp) / "nothing"))

    def test_open_for_run_prefers_recorded_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._legacy_run(Path(tmp))
            with TrajectoryDB(Path(tmp) / "trajectories.db") as db:
                db.import_run_dir(run)
            with trajectories.open_for_run(run) as db:
                self.assertEqual(db.path, str(Path(tmp) / "trajectories.db"))
            self.assertEqual(trajectories.resolve_db_path(Path(tmp)), Path(tmp) / "trajectories.db")
            self.assertEqual(trajectories.resolve_db_path(run), Path(tmp) / "trajectories.db")


class SupervisorIntegrationTest(unittest.TestCase):
    """A real mock run records everything into the database, and report/CLI read it."""

    def test_mock_run_populates_db(self):
        from sia import cli, report
        from sia.config import REPO_ROOT, load
        from sia.supervisor import Supervisor

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            summary = Supervisor(load(REPO_ROOT / "experiments" / "mock_smoke.toml"), run_dir, verbose=False).run()
            db_path = Path(tmp) / "trajectories.db"
            self.assertEqual(summary["trajectory_db"], str(db_path))
            with TrajectoryDB(db_path, readonly=True) as db:
                r = db.run("run")
                self.assertEqual((r["mean_score"], r["tasks_graded"]), (1.0, 3))
                jsonl_calls = (run_dir / "llm_calls.jsonl").read_text().splitlines()
                self.assertEqual(len(db.llm_calls("run")), len(jsonl_calls))
                self.assertEqual(len(db.events("run")), len((run_dir / "events.jsonl").read_text().splitlines()))
                self.assertEqual([t["task_id"] for t in db.tasks("run")], ["t1", "t2", "t3"])
                self.assertEqual([g["changed_by"] for g in db.generations("run")], [None, "agent"])
                tools = db.tool_calls("run")
                # A submit's result is never sent back (the next task starts a new conversation).
                self.assertTrue(tools and all(t["output"] is not None for t in tools if t["name"] != "submit"))
            a = report.analyze(run_dir)
            self.assertTrue(a["signals"]["harness_modified"])
            self.assertEqual(a["restarts"]["sigterm(143)"], 1)
            out = io.StringIO()
            with redirect_stdout(out):
                cli.main(["db", tmp, "--sql", "SELECT COUNT(*) AS n FROM tasks", "--json"])
            self.assertEqual(json.loads(out.getvalue()), [{"n": 3}])


if __name__ == "__main__":
    unittest.main()
