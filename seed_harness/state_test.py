import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HARNESS = str(Path(__file__).resolve().parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import state as state_mod  # noqa: E402
from state import State  # noqa: E402


class StateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ckpt = Path(self._tmp.name) / "state" / "checkpoint.json"
        patcher = mock.patch.object(state_mod, "CHECKPOINT", self.ckpt)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_without_checkpoint(self):
        st, resumed = state_mod.load()
        self.assertFalse(resumed)
        self.assertEqual(st, State())

    def test_save_and_load_round_trip(self):
        st = State(task_id="t1", messages=[{"role": "user", "content": []}], submitted=True, notice_offset=2, pending=["p"])
        st.save()
        self.assertEqual(sorted(p.name for p in self.ckpt.parent.iterdir()), ["checkpoint.json"])
        loaded, resumed = state_mod.load()
        self.assertTrue(resumed)
        self.assertEqual(loaded, st)

    def test_load_corrupt_or_incompatible_checkpoint(self):
        self.ckpt.parent.mkdir(parents=True)
        self.ckpt.write_text("{bad")
        self.assertEqual(state_mod.load(), (State(), False))
        self.ckpt.write_text(json.dumps({"unknown_field": 1}))
        self.assertEqual(state_mod.load(), (State(), False))

    def test_start_task_resets_conversation(self):
        st = State(task_id="old", messages=[{"role": "user", "content": "x"}], submitted=True)
        st.start_task({"task_id": "new", "prompt": "Do it"})
        self.assertEqual((st.task_id, st.submitted), ("new", False))
        self.assertEqual(st.messages, [{"role": "user", "content": [{"type": "text", "text": "Do it"}]}])

    def test_close_dangling_tool_calls(self):
        st = State()
        st.close_dangling_tool_calls("r")  # no messages: no-op
        self.assertEqual(st.messages, [])
        st.messages = [{"role": "user", "content": []}]
        st.close_dangling_tool_calls("r")  # last turn is the user's: no-op
        self.assertEqual(len(st.messages), 1)
        st.messages.append({"role": "assistant", "content": [{"type": "text", "text": "hi"}]})
        st.close_dangling_tool_calls("r")  # no tool calls: no-op
        self.assertEqual(len(st.messages), 2)
        st.messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": "a"}, {"type": "text", "text": "x"}, {"type": "tool_use", "id": "b"}]})
        st.close_dangling_tool_calls("Interrupted")
        self.assertEqual(st.messages[-1], {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "Interrupted", "is_error": True},
            {"type": "tool_result", "tool_use_id": "b", "content": "Interrupted", "is_error": True},
        ]})


if __name__ == "__main__":
    unittest.main()
