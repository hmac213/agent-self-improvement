import sys
import unittest
from pathlib import Path

HARNESS = str(Path(__file__).resolve().parent.parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

from tools import Context, RestartRequested, restart  # noqa: E402


class RestartToolTest(unittest.TestCase):
    def test_raises_restart_requested(self):
        with self.assertRaises(RestartRequested):
            restart.run({}, Context(workdir=Path("/w"), task_id="t", cfg={}))
        self.assertEqual(restart.SPEC["name"], "restart")


if __name__ == "__main__":
    unittest.main()
