"""Unit tests for the supervisor's control flow, with the sandbox and proxy
replaced by fakes. End-to-end runs live in tests/test_e2e_mock.py."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sia import supervisor as sup_mod
from sia.config import Config
from sia.proxy import Stats
from sia.sandbox import SandboxPaths
from sia.supervisor import Supervisor
from sia.tasks import Grade, Task


class FakeProxy:
    def __init__(self):
        self.stats = Stats()
        self.exhausted = False
        self.task_exhausted = False
        self.task_rejected = 0
        self.context = {}
        self.port = 1234
        self.token = "tok"
        self.started = []
        self.stopped = False

    def start_task(self, task_id, limit):
        self.started.append((task_id, limit))

    def stop(self):
        self.stopped = True


def fake_sandbox():
    sb = mock.Mock()
    sb.paths = SandboxPaths(agent_home="/home", env_dir="/env", workspace="/ws", runtime="/rt")
    sb.python = "py"
    sb.files = {}
    sb.write_text_atomic.side_effect = lambda path, text: sb.files.__setitem__(path, text)
    sb.read_text.side_effect = lambda path: sb.files.get(path)
    sb.proxy_url.side_effect = lambda port: f"http://host:{port}"
    sb.exec.return_value = (0, "")
    return sb


class SupervisorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = Config()
        self.sup = Supervisor(self.cfg, self.root / "run-x", verbose=False)
        self.sup.run_dir.mkdir()
        self.sup.gens_dir.mkdir()
        self.sup.sb = fake_sandbox()
        self.sup.proxy = FakeProxy()
        self.sup.t_start = time.time()
        self.task = Task("t", self.root / "task_t", "# T\nDo it.")
        self.sup.schedule = [("t1", self.task), ("t2", self.task)]

    def tearDown(self):
        self._tmp.cleanup()

    def events(self):
        return [json.loads(line) for line in self.sup.events_path.read_text().splitlines()]

    def types(self):
        return [e["type"] for e in self.events()]


class InitAndSetupTest(SupervisorTestBase):
    def test_init_seed_selection(self):
        self.assertFalse(self.sup.inherited)
        self.assertEqual(self.sup.seed_path, self.cfg.resolve("seed_harness"))
        other = Supervisor(self.cfg, self.root / "r2", seed_harness=self.root / "s")
        self.assertTrue(other.inherited)
        self.assertEqual((other.seed_path, other.run_id), (self.root / "s", "r2"))

    def make_seed(self):
        seed = self.root / "seed"
        (seed / "tools" / "__pycache__").mkdir(parents=True)
        (seed / "tools" / "restart.py").write_text("")
        (seed / "tools" / "restart_test.py").write_text("")
        (seed / "tools" / "__pycache__" / "x.pyc").write_text("")
        (seed / "config.json").write_text(json.dumps({"keep": 1, "model": "old"}))
        return seed

    def test_render_seed_specialises_for_experiment(self):
        self.sup.seed_path = self.make_seed()
        self.cfg.experiment.affordance = 1
        dest = self.root / "out"
        self.sup._render_seed(dest)
        self.assertFalse((dest / "tools" / "restart.py").exists())
        self.assertFalse((dest / "tools" / "__pycache__").exists())
        self.assertFalse((dest / "tools" / "restart_test.py").exists())  # seed tests stay on the host
        self.assertIn("/home/harness", (dest / "system_prompt.md").read_text())
        hcfg = json.loads((dest / "config.json").read_text())
        self.assertEqual(hcfg, {"keep": 1, "model": self.cfg.model.name, "max_tokens": self.cfg.model.max_tokens, "effort": self.cfg.model.effort})

    def test_render_seed_keeps_restart_tool_and_custom_prompt(self):
        self.sup.seed_path = self.make_seed()
        self.cfg.experiment.affordance = 2
        self.cfg.experiment.system_prompt = "CUSTOM"
        dest = self.root / "out"
        self.sup._render_seed(dest)
        self.assertTrue((dest / "tools" / "restart.py").exists())
        self.assertEqual((dest / "system_prompt.md").read_text(), "CUSTOM")

    def test_render_seed_inherited_is_verbatim(self):
        self.sup.seed_path, self.sup.inherited = self.make_seed(), True
        dest = self.root / "out"
        self.sup._render_seed(dest)
        self.assertTrue((dest / "tools" / "restart.py").exists())
        self.assertFalse((dest / "system_prompt.md").exists())
        self.assertTrue((dest / "tools" / "restart_test.py").exists())  # agent-written files are kept
        self.assertEqual(json.loads((dest / "config.json").read_text())["model"], "old")

    def test_ensure_deps(self):
        sb = self.sup.sb
        self.sup._ensure_deps()
        sb.check.assert_not_called()
        sb.exec.return_value = (1, "no pytest")
        self.sup._ensure_deps()
        sb.check.assert_called_once_with("py -m pip install -q pytest", timeout=600)
        self.assertIn("installing_deps", self.types())

    def test_make_proxy_mock(self):
        self.cfg.model.upstream, self.cfg.model.mock_policy = "mock", "solve"
        self.cfg.model.price_per_mtok = [1.0, 2.0]
        with mock.patch.object(sup_mod, "LLMProxy") as cls:
            self.sup._make_proxy()
        kw = cls.call_args.kwargs
        self.assertEqual((kw["provider"], kw["api_key"], kw["force_model"], kw["prices"]), ("anthropic", None, self.cfg.model.name, (1.0, 2.0)))
        self.assertIs(kw["mock"], sup_mod.mock_llm.solve)
        cls.return_value.start.assert_called_once()

    def test_make_proxy_reads_host_key(self):
        self.cfg.model.upstream, self.cfg.model.model_policy = "gemini", "allow"
        env = {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": "g-key"}
        with mock.patch.object(sup_mod, "LLMProxy") as cls, mock.patch.dict(os.environ, env):
            self.sup._make_proxy()
        kw = cls.call_args.kwargs
        self.assertEqual((kw["provider"], kw["api_key"], kw["force_model"], kw["mock"]), ("gemini", "g-key", None, None))

    def test_make_proxy_errors(self):
        self.cfg.model.upstream = "nope"
        with self.assertRaisesRegex(ValueError, "unknown model.upstream"):
            self.sup._make_proxy()
        self.cfg.model.upstream = "openai"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}), self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            self.sup._make_proxy()


class TaskFlowTest(SupervisorTestBase):
    def test_task_prompt_feedback(self):
        self.cfg.limits.task_llm_calls = 7
        p = self.sup._task_prompt(1, self.task, "/ws/t1")
        self.assertIn("# Task 1 of 2", p)
        self.assertIn("Working directory: /ws/t1", p)
        self.assertIn("at most 7 model calls", p)
        self.assertNotIn("previous task", p)
        self.sup.grades = [Grade("t0", 1, 2, failures=[{"test": "test_a", "message": "bad"}])]
        self.assertIn("(t0): 1/2 hidden tests passed", self.sup._task_prompt(2, self.task, "/ws"))
        self.assertNotIn("test_a", self.sup._task_prompt(2, self.task, "/ws"))
        self.cfg.tasks.feedback = "failures"
        self.assertIn("- test_a: bad", self.sup._task_prompt(2, self.task, "/ws"))
        self.cfg.tasks.feedback = "none"
        self.assertNotIn("previous task", self.sup._task_prompt(2, self.task, "/ws"))

    def test_post_next_task_then_done(self):
        with mock.patch.object(sup_mod.tasks_mod, "prepare_workdir") as prep:
            self.sup._post_next_task()
        prep.assert_called_once_with(self.sup.sb, self.task, "/ws/t1")
        posted = json.loads(self.sup.sb.files["/env/task.json"])
        self.assertEqual((posted["task_id"], posted["seq"], posted["total"], posted["workdir"]), ("t1", 1, 2, "/ws/t1"))
        self.assertEqual(self.sup.current["id"], "t1")
        self.assertEqual(self.sup.proxy.started, [("t1", self.cfg.limits.task_llm_calls)])
        self.sup.next_index = 2
        self.sup._post_next_task()
        self.assertEqual(json.loads(self.sup.sb.files["/env/task.json"]), {"done": True})
        self.assertIsNone(self.sup.current)
        self.assertIsNotNone(self.sup.finishing_since)
        self.assertEqual(self.types(), ["task_posted", "tasks_done"])

    def test_post_next_task_when_stopping(self):
        self.sup.stop_reason = "wall_clock"
        self.sup._post_next_task()
        self.assertEqual(json.loads(self.sup.sb.files["/env/task.json"]), {"done": True})

    def start_current(self):
        self.sup.current = {"id": "t1", "task": self.task, "workdir": "/ws/t1", "t0": time.time(), "calls0": 0}
        self.sup.next_index = 1

    def test_finish_task_records_grade(self):
        self.start_current()
        self.sup.proxy.stats.calls = 4
        with mock.patch.object(sup_mod.tasks_mod, "grade", return_value=Grade("t", 2, 3)), \
                mock.patch.object(self.sup, "_post_next_task") as nxt:
            self.sup._finish_task("submitted")
        nxt.assert_called_once()
        self.assertEqual(self.sup.grades[0].task_id, "t1")
        ev = self.events()[-1]
        self.assertEqual((ev["type"], ev["reason"], ev["passed"], ev["llm_calls"]), ("graded", "submitted", 2, 4))
        res = json.loads((self.sup.run_dir / "results.jsonl").read_text())
        self.assertEqual(res, {"task_id": "t1", "reason": "submitted", "passed": 2, "total": 3, "llm_calls": 4})

    def test_check_submission(self):
        self.start_current()
        with mock.patch.object(self.sup, "_finish_task") as fin:
            self.sup._check_submission()  # nothing submitted
            fin.assert_not_called()
            for raw in ("not json", json.dumps(["list"]), json.dumps({"task_id": "other"})):
                self.sup.sb.files["/env/submit.json"] = raw
                self.sup._check_submission()
            fin.assert_not_called()
            self.assertEqual(self.types().count("bad_submission"), 3)
            self.sup.sb.files["/env/submit.json"] = json.dumps({"task_id": "t1"})
            self.sup._check_submission()
            fin.assert_called_once_with("submitted")
        self.sup.sb.exec.assert_called_with("rm -f /env/submit.json")


class LimitsTest(SupervisorTestBase):
    def setUp(self):
        super().setUp()
        self.sup.current = {"id": "t1", "task": self.task, "workdir": "/ws/t1", "t0": time.time(), "calls0": 0}
        self.finish = mock.patch.object(self.sup, "_finish_task").start()
        self.post = mock.patch.object(self.sup, "_post_next_task").start()
        self.addCleanup(mock.patch.stopall)

    def test_no_limits_hit(self):
        self.sup._check_limits()
        self.finish.assert_not_called()
        self.assertIsNone(self.sup.stop_reason)

    def test_tracks_last_call(self):
        self.sup.last_call_at = 0
        self.sup.proxy.stats.calls = 3
        self.sup._check_limits()
        self.assertEqual(self.sup.last_call_count, 3)
        self.assertGreater(self.sup.last_call_at, 0)

    def test_run_budget_waits_until_settled(self):
        self.sup.proxy.exhausted = True
        self.sup._check_limits()  # just exhausted: give the harness time
        self.assertIsNone(self.sup.stop_reason)
        self.sup.proxy.task_rejected = 1
        self.sup._check_limits()
        self.assertEqual(self.sup.stop_reason, "run_budget")
        self.finish.assert_called_once_with("run_budget")

    def test_run_budget_without_current_task(self):
        self.sup.current = None
        self.sup.proxy.exhausted = True
        self.sup._check_limits()
        self.assertEqual(self.sup.stop_reason, "run_budget")
        self.post.assert_called_once()

    def test_wall_clock(self):
        self.sup.t_start = time.time() - self.cfg.limits.wall_seconds - 1
        self.sup._check_limits()
        self.assertEqual(self.sup.stop_reason, "wall_clock")
        self.assertIn("stopping", self.types())

    def test_task_call_budget(self):
        self.sup.proxy.task_exhausted = True
        self.sup.last_call_at = time.time() - self.cfg.limits.budget_grace_seconds - 1
        self.sup._check_limits()
        self.finish.assert_called_once_with("task_call_budget")
        self.assertIn("used its budget", json.loads(self.sup.sb.files["/env/notices.jsonl"].splitlines()[0])["text"])

    def test_task_time_limit(self):
        self.sup.current["t0"] = time.time() - self.cfg.limits.task_wall_seconds - 1
        self.sup._check_limits()
        self.finish.assert_called_once_with("task_time_limit")
        self.assertEqual(len(self.sup.notices), 1)


class GenerationTest(SupervisorTestBase):
    def test_harness_env(self):
        env = self.sup._harness_env(4)
        self.assertEqual((env["HOME"], env["SIA_ENV_DIR"], env["SIA_GENERATION"], env["SIA_PYTHON"]), ("/home", "/env", "4", "py"))
        self.assertEqual((env["SIA_LLM_URL"], env["SIA_LLM_TOKEN"]), ("http://host:1234", "tok"))
        self.assertEqual((env["ANTHROPIC_API_KEY"], env["OPENAI_API_KEY"], env["GEMINI_API_KEY"]), ("tok", "", ""))

    def test_snapshot_writes_diffs_only_on_change(self):
        prev = self.root / "prev"
        prev.mkdir()
        (prev / "a.py").write_text("1\n")
        self.sup.seed_snapshot = prev

        def download(remote, local, exclude=()):
            local.mkdir(parents=True)
            (local / "a.py").write_text(content)

        self.sup.sb.download_dir.side_effect = download
        content = "1\n"
        snap, h, stats = self.sup._snapshot(0, prev)
        self.assertIsNone(stats)
        self.assertEqual(snap, self.sup.gens_dir / "gen_000" / "harness")
        self.assertFalse((self.sup.gens_dir / "gen_000" / "diff_vs_prev.patch").exists())
        content = "2\n"
        _, h2, stats = self.sup._snapshot(1, snap)
        self.assertNotEqual(h, h2)
        self.assertEqual(stats["modified"], ["a.py"])
        self.assertIn("+2", (self.sup.gens_dir / "gen_001" / "diff_vs_prev.patch").read_text())
        self.assertTrue((self.sup.gens_dir / "gen_001" / "diff_vs_seed.patch").exists())

    def test_run_generation_polls_until_exit(self):
        sb = self.sup.sb
        sb.poll_harness.side_effect = [None, None, 0]
        sb.files["/rt/harness-gen002.log"] = "LOG"
        (self.sup.gens_dir / "gen_002").mkdir()
        with mock.patch.object(sup_mod.time, "sleep"), \
                mock.patch.object(self.sup, "_check_submission") as sub, mock.patch.object(self.sup, "_check_limits"):
            code = self.sup._run_generation(2)
        self.assertEqual(code, 0)
        self.assertEqual(sb.start_harness.call_args[0][1], "/rt/harness-gen002.log")
        self.assertEqual(sub.call_count, 3)  # twice while running, once after exit
        self.assertEqual((self.sup.gens_dir / "gen_002" / "harness.log").read_text(), "LOG")
        sb.stop_harness.assert_not_called()

    def test_run_generation_kills_after_finish_grace_and_stall(self):
        sb = self.sup.sb
        (self.sup.gens_dir / "gen_000").mkdir()
        sb.poll_harness.side_effect = [None, 143]
        self.sup.finishing_since = time.time() - self.cfg.limits.finish_grace_seconds - 1
        with mock.patch.object(sup_mod.time, "sleep"), mock.patch.object(self.sup, "_check_submission"), \
                mock.patch.object(self.sup, "_check_limits"):
            self.assertEqual(self.sup._run_generation(0), 143)
            sb.stop_harness.assert_called_once()
            self.sup.finishing_since = None
            sb.poll_harness.side_effect = [None, -1]
            with mock.patch.object(sup_mod.time, "time", side_effect=[0.0, self.cfg.limits.stall_seconds + 1, 1e9, 1e9]):
                self.sup._run_generation(0)
        self.assertEqual(sb.stop_harness.call_count, 2)
        self.assertEqual([e["reason"] for e in self.events() if e["type"] == "killing_harness"], ["finish_grace_expired", "stalled"])

    def run_loop(self, outcomes):
        """outcomes: (exit code, llm calls, runtime) per generation; runs the loop with faked generations."""
        self.sup.seed_snapshot = self.root / "seed"
        it = iter(outcomes)
        clock = [0.0]

        def run_generation(gen):
            code, calls, runtime = next(it)
            self.sup.proxy.stats.calls += calls
            clock[0] += runtime
            log = self.sup.gens_dir / f"gen_{gen:03d}"
            log.mkdir(parents=True, exist_ok=True)
            (log / "harness.log").write_text("Traceback: boom")
            if calls == 99:
                self.sup.finishing_since = 1.0
            return code

        snaps = lambda gen, prev: (self.root / f"snap{gen}", f"h{gen}", None if gen == 0 else {"modified": ["a"]})  # noqa: E731
        with mock.patch.object(self.sup, "_snapshot", side_effect=snaps), \
                mock.patch.object(self.sup, "_run_generation", side_effect=run_generation), \
                mock.patch.object(sup_mod.time, "time", side_effect=lambda: clock[0]):
            self.sup._generation_loop()

    def test_generation_loop_rolls_back_after_boot_failures(self):
        self.cfg.limits.boot_failures_before_rollback = 2
        # gen0 works, gen1+gen2 fail to boot -> rollback to gen0, gen3 finishes the run.
        self.run_loop([(143, 3, 1.0), (1, 0, 0.1), (1, 0, 0.1), (0, 99, 1.0)])
        self.sup.sb.upload_dir.assert_called_once_with(self.root / "snap0", "/home/harness", replace=True)
        rb = [e for e in self.events() if e["type"] == "rollback"]
        self.assertEqual((rb[0]["to_generation"], rb[0]["failures"]), (0, 2))
        self.assertIn("Traceback: boom", self.sup.notices[0]["text"])
        launches = [e for e in self.events() if e["type"] == "launch"]
        self.assertEqual([e["harness_changed"] for e in launches], [False, True, True, True])
        self.assertIsNone(self.sup.stop_reason)

    def test_generation_loop_max_generations(self):
        self.cfg.limits.max_generations = 2
        self.run_loop([(143, 1, 1.0), (143, 1, 1.0)])
        self.assertEqual(self.sup.stop_reason, "max_generations")
        self.sup.sb.upload_dir.assert_not_called()


class RunAndFinalizeTest(SupervisorTestBase):
    def test_finalize_collects_and_summarises(self):
        self.sup.seed_snapshot = self.root / "seed"
        self.sup.seed_snapshot.mkdir()
        (self.sup.seed_snapshot / "a.py").write_text("1\n")

        def download(remote, local, exclude=()):
            local.mkdir(parents=True)
            if remote == "/home":
                (local / "harness").mkdir()
                (local / "harness" / "a.py").write_text("2\n")

        self.sup.sb.download_dir.side_effect = download
        self.sup.grades = [Grade("t1", 1, 1), Grade("t2", 0, 2)]
        self.sup.proxy.stats = Stats(calls=5, cost_usd=0.123456, models_requested={"m": 5})
        summary = self.sup._finalize()
        self.sup.sb.teardown.assert_called_once()
        self.assertTrue(self.sup.proxy.stopped)
        self.assertEqual((summary["tasks_graded"], summary["mean_score"], summary["llm_calls"], summary["cost_usd"]), (2, 0.5, 5, 0.1235))
        self.assertEqual(summary["scores"], {"t1": 1.0, "t2": 0.0})
        self.assertEqual(summary["harness_vs_seed"]["modified"], ["a.py"])
        self.assertTrue((self.sup.run_dir / "final" / "harness_vs_seed.patch").exists())
        self.assertEqual(json.loads((self.sup.run_dir / "summary.json").read_text()), summary)
        self.assertEqual(self.types()[-1], "run_end")

    def test_finalize_tolerates_errors_and_keep(self):
        self.cfg.sandbox.keep = True
        self.sup.sb.download_dir.side_effect = RuntimeError("gone")
        summary = self.sup._finalize()
        self.assertIn("finalize_error", self.types())
        self.sup.sb.teardown.assert_not_called()
        self.assertEqual(summary["mean_score"], 0.0)
        self.assertNotIn("harness_vs_seed", summary)

    def test_run_wires_everything_together(self):
        sb, proxy = fake_sandbox(), FakeProxy()
        sup = Supervisor(self.cfg, self.root / "fresh", verbose=False)
        with mock.patch.object(sup_mod, "make_sandbox", return_value=sb) as mk, \
                mock.patch.object(sup, "_make_proxy", return_value=proxy), \
                mock.patch.object(sup, "_ensure_deps"), \
                mock.patch.object(sup_mod.tasks_mod, "load_suite", return_value=[self.task]), \
                mock.patch.object(sup, "_post_next_task") as post, \
                mock.patch.object(sup, "_generation_loop") as loop:
            summary = sup.run()
        mk.assert_called_once_with(self.cfg.sandbox, sup.run_dir, "fresh")
        sb.setup.assert_called_once()
        self.assertEqual(sb.upload_dir.call_args[0][1:], ("/home/harness",))
        self.assertTrue((sup.run_dir / "seed_harness" / "main.py").exists())
        self.assertFalse(list((sup.run_dir / "seed_harness").rglob("*_test.py")))
        self.assertEqual(json.loads((sup.run_dir / "config.json").read_text())["model"]["name"], self.cfg.model.name)
        self.assertEqual(sup.schedule, [("t", self.task)])
        post.assert_called_once()
        loop.assert_called_once()
        self.assertEqual(summary["run_id"], "fresh")
        self.assertTrue(proxy.stopped)

    def test_run_records_error_and_finalizes(self):
        sb = fake_sandbox()
        sb.setup.side_effect = RuntimeError("no docker")
        sup = Supervisor(self.cfg, self.root / "fresh", verbose=False)
        with mock.patch.object(sup_mod, "make_sandbox", return_value=sb), mock.patch.object(sup, "_make_proxy", return_value=FakeProxy()):
            with self.assertRaisesRegex(RuntimeError, "no docker"):
                sup.run()
        types = [json.loads(line)["type"] for line in sup.events_path.read_text().splitlines()]
        self.assertIn("run_error", types)
        self.assertEqual(types[-1], "run_end")
        self.assertTrue((sup.run_dir / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
