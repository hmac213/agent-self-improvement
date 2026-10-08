"""Tests for the harness mailbox. The harness imports its modules by bare name
(it runs with its own directory on sys.path), so the tests do the same."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HARNESS = str(Path(__file__).resolve().parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import env  # noqa: E402


class EnvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        patcher = mock.patch.object(env, "ENV_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_current_task(self):
        self.assertIsNone(env.current_task())
        (self.dir / "task.json").write_text("{partial")
        self.assertIsNone(env.current_task())
        (self.dir / "task.json").write_text(json.dumps({"task_id": "t1"}))
        self.assertEqual(env.current_task(), {"task_id": "t1"})

    def test_submit_is_atomic(self):
        env.submit("t1")
        self.assertEqual(json.loads((self.dir / "submit.json").read_text()), {"task_id": "t1"})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["submit.json"])

    def test_read_notices(self):
        self.assertEqual(env.read_notices(3), ([], 3))
        lines = [json.dumps({"text": "a"}), "garbage", json.dumps({"other": 1}), json.dumps({"text": "b"})]
        (self.dir / "notices.jsonl").write_text("\n".join(lines) + "\n")
        self.assertEqual(env.read_notices(0), (["a", "b"], 4))
        self.assertEqual(env.read_notices(1), (["b"], 4))
        self.assertEqual(env.read_notices(4), ([], 4))


if __name__ == "__main__":
    unittest.main()
