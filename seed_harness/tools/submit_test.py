import sys
import unittest
from pathlib import Path
from unittest import mock

HARNESS = str(Path(__file__).resolve().parent.parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

from tools import Context, submit  # noqa: E402


class SubmitToolTest(unittest.TestCase):
    def test_submits_current_task_and_marks_context(self):
        ctx = Context(workdir=Path("/w"), task_id="t7", cfg={})
        with mock.patch.object(submit.env, "submit") as env_submit:
            out = submit.run({}, ctx)
        env_submit.assert_called_once_with("t7")
        self.assertTrue(ctx.submitted)
        self.assertIn("Submitted", out)
        self.assertEqual(submit.SPEC["name"], "submit")


if __name__ == "__main__":
    unittest.main()
