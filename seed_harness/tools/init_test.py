"""Tests for seed_harness/tools/__init__.py (the tool registry)."""

import sys
import unittest
from pathlib import Path

HARNESS = str(Path(__file__).resolve().parent.parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import tools  # noqa: E402


class RegistryTest(unittest.TestCase):
    def test_load_all_finds_seed_tools(self):
        loaded = tools.load_all()
        self.assertEqual(sorted(loaded), ["bash", "restart", "submit"])  # modules without SPEC/run are skipped
        for name, mod in loaded.items():
            self.assertEqual(mod.SPEC["name"], name)
            self.assertEqual(mod.SPEC["input_schema"]["type"], "object")
            self.assertTrue(callable(mod.run))

    def test_context_defaults(self):
        ctx = tools.Context(workdir=Path("/w"), task_id="t", cfg={})
        self.assertFalse(ctx.submitted)

    def test_truncate(self):
        self.assertEqual(tools.truncate("short", 10), "short")
        self.assertEqual(tools.truncate("x" * 10, 10), "x" * 10)
        out = tools.truncate("a" * 10 + "b" * 10, 10)
        self.assertEqual(out, "aaaaa\n... [10 characters omitted] ...\nbbbbb")

    def test_restart_requested_is_exception(self):
        self.assertTrue(issubclass(tools.RestartRequested, Exception))


if __name__ == "__main__":
    unittest.main()
