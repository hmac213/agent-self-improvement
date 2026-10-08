import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HARNESS = str(Path(__file__).resolve().parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import main  # noqa: E402
import state as state_mod  # noqa: E402
import tools as tools_mod  # noqa: E402
from llm import LLMError  # noqa: E402


def tool(name, fn):
    return SimpleNamespace(SPEC={"name": name}, run=fn)


def tool_use(name, id_="u1", input=None):
    return {"type": "tool_use", "id": id_, "name": name, "input": input or {}}


def raise_restart(inp, ctx):
    raise tools_mod.RestartRequested()


def do_submit(inp, ctx):
    ctx.submitted = True
    return "submitted"


def explode(inp, ctx):
    raise OSError("disk full")


class AddUserTextTest(unittest.TestCase):
    def test_appends_to_trailing_user_turn_or_starts_one(self):
        st = state_mod.State(messages=[{"role": "assistant", "content": []}])
        main.add_user_text(st, ["a"])
        main.add_user_text(st, ["b", "c"])
        self.assertEqual(st.messages[1], {"role": "user", "content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"}, {"type": "text", "text": "c"}]})
        self.assertEqual(len(st.messages), 2)


class RunToolsTest(unittest.TestCase):
    def setUp(self):
        self.st = state_mod.State()
        self.ctx = tools_mod.Context(workdir=Path("."), task_id="t", cfg={})

    def test_results_and_errors(self):
        toolset = {"echo": tool("echo", lambda inp, ctx: inp["x"]), "bad": tool("bad", explode)}
        content = [{"type": "text", "text": "hi"}, tool_use("echo", "1", {"x": "out"}), tool_use("bad", "2"), tool_use("nope", "3")]
        self.assertFalse(main.run_tools(self.st, content, toolset, self.ctx))
        self.assertEqual(self.st.messages, [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "out", "is_error": False},
            {"type": "tool_result", "tool_use_id": "2", "content": "OSError: disk full", "is_error": True},
            {"type": "tool_result", "tool_use_id": "3", "content": "ValueError: unknown tool 'nope'", "is_error": True},
        ]}])

    def test_restart_requested(self):
        content = [tool_use("restart", "1"), tool_use("echo", "2")]
        toolset = {"restart": tool("restart", raise_restart), "echo": tool("echo", lambda i, c: "x")}
        self.assertTrue(main.run_tools(self.st, content, toolset, self.ctx))
        self.assertEqual([r["content"] for r in self.st.messages[0]["content"]], ["Restarting harness...", "x"])


class MainLoopTest(unittest.TestCase):
    """Drives main() with a scripted mailbox and model."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "config.json").write_text(json.dumps({"model": "m", "max_tokens": 5}))
        (root / "system_prompt.md").write_text("SYSTEM")
        self.workdir = root / "work"
        self.ckpt = root / "state" / "checkpoint.json"
        self.task = {"task_id": "t1", "prompt": "Do it", "workdir": str(self.workdir)}
        self.toolset = {"submit": tool("submit", do_submit), "restart": tool("restart", raise_restart),
                        "echo": tool("echo", lambda inp, ctx: "echoed")}
        self.requests = []
        self.responses = []
        self.llm = mock.Mock()
        self.llm.call.side_effect = self.fake_call
        self.notices = mock.Mock(return_value=([], 0))
        self.tasks = []
        for target, attr, value in [
            (main, "HERE", root), (state_mod, "CHECKPOINT", self.ckpt), (main.time, "sleep", mock.Mock()),
            (main, "log", mock.Mock()), (main.env, "read_notices", self.notices),
            (main.env, "current_task", mock.Mock(side_effect=lambda: self.tasks.pop(0))),
        ]:
            mock.patch.object(target, attr, value).start()
        self.LLM = mock.patch.object(main, "LLM", return_value=self.llm).start()
        mock.patch.object(tools_mod, "load_all", return_value=self.toolset).start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(self._tmp.cleanup)

    def fake_call(self, system, messages, tools):
        self.requests.append((system, copy.deepcopy(messages), [t["name"] for t in tools]))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def saved_state(self):
        return json.loads(self.ckpt.read_text())

    def test_waits_for_task_then_exits_when_done(self):
        self.tasks = [None, {"done": True}]
        self.assertEqual(main.main(), 0)
        main.time.sleep.assert_called_once_with(1)
        self.assertTrue(self.ckpt.exists())
        self.LLM.assert_called_once_with({"model": "m", "max_tokens": 5})
        self.llm.call.assert_not_called()

    def test_solves_and_submits_a_task(self):
        self.tasks = [self.task, self.task, self.task, {"done": True}]
        self.responses = [
            {"content": [{"type": "text", "text": "thinking"}, tool_use("echo", "a")], "stop_reason": "tool_use"},
            {"content": [tool_use("submit", "b")], "stop_reason": "tool_use", "usage": {"input_tokens": 1}},
        ]
        self.assertEqual(main.main(), 0)
        system, first, tool_names = self.requests[0]
        self.assertEqual((system, tool_names), ("SYSTEM", ["submit", "restart", "echo"]))
        self.assertEqual(first, [{"role": "user", "content": [{"type": "text", "text": "Do it"}]}])
        second = self.requests[1][1]
        self.assertEqual(second[-1]["content"][0]["content"], "echoed")
        st = self.saved_state()
        self.assertEqual((st["task_id"], st["submitted"]), ("t1", True))
        self.assertEqual(len(self.requests), 2)  # no model call after submitting

    def test_restart_tool_exits_75(self):
        self.tasks = [self.task]
        self.responses = [{"content": [tool_use("restart")], "stop_reason": "tool_use"}]
        self.assertEqual(main.main(), 75)
        self.assertEqual(self.saved_state()["messages"][-1]["content"][0]["content"], "Restarting harness...")

    def test_resume_closes_dangling_calls_and_survives_api_errors(self):
        st = state_mod.State(task_id="t1", messages=[
            {"role": "user", "content": [{"type": "text", "text": "Do it"}]},
            {"role": "assistant", "content": [tool_use("echo", "dangling")]}])
        self.ckpt.parent.mkdir(parents=True)
        st.save()
        self.tasks = [self.task, self.task, {"done": True}]
        self.responses = [LLMError(403, "budget"), {"content": [], "stop_reason": "refusal"}]
        self.notices.side_effect = [(["Task graded"], 1), ([], 1)]
        with mock.patch.dict("os.environ", {"SIA_GENERATION": "3"}):
            self.assertEqual(main.main(), 0)
        main.time.sleep.assert_any_call(5)
        last_user = self.requests[0][1][-1]["content"]
        self.assertEqual(last_user[0]["tool_use_id"], "dangling")
        self.assertIn("Interrupted", last_user[0]["content"])
        self.assertEqual(last_user[1]["text"], "[Harness restarted (generation 3); resumed from checkpoint.]")
        self.assertEqual(last_user[2]["text"], "[Notice] Task graded")
        # Pending text is sent once, even though the first call failed.
        self.assertEqual(self.requests[1][1], self.requests[0][1])
        msgs = self.saved_state()["messages"]
        self.assertEqual(msgs[-1]["content"], [{"type": "text", "text": "(empty response, stop_reason=refusal)"}])
        self.assertEqual(self.saved_state()["notice_offset"], 1)

    def test_nudges_after_text_only_turn(self):
        self.tasks = [self.task, self.task, self.task, {"done": True}]
        self.responses = [{"content": [{"type": "text", "text": "hmm"}], "stop_reason": "end_turn"},
                          {"content": [tool_use("submit")], "stop_reason": "tool_use"}]
        self.assertEqual(main.main(), 0)
        self.assertEqual(self.requests[1][1][-1], {"role": "user", "content": [{"type": "text", "text": main.NUDGE}]})


if __name__ == "__main__":
    unittest.main()
