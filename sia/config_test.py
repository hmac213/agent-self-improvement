import tempfile
import unittest
from pathlib import Path

from sia import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "exp.toml"

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_when_empty(self):
        self.path.write_text("")
        cfg = config.load(self.path)
        self.assertEqual(cfg, config.Config())
        self.assertEqual(cfg.sandbox.kind, "local")

    def test_load_sections_and_overrides(self):
        self.path.write_text('[experiment]\nname = "x"\naffordance = 2\n[limits]\ntask_llm_calls = 5\n')
        cfg = config.load(self.path, {"model.upstream": "mock", "limits.task_llm_calls": 7, "tasks.epochs": 3})
        self.assertEqual(cfg.experiment.name, "x")
        self.assertEqual(cfg.experiment.affordance, 2)
        self.assertEqual(cfg.model.upstream, "mock")
        self.assertEqual(cfg.limits.task_llm_calls, 7)
        self.assertEqual(cfg.tasks.epochs, 3)
        self.assertIsInstance(cfg.model, config.ModelCfg)

    def test_unknown_keys_rejected(self):
        self.path.write_text("[model]\nnope = 1\n")
        with self.assertRaisesRegex(ValueError, r"unknown keys for \[ModelCfg\]"):
            config.load(self.path)
        self.path.write_text("[bogus]\na = 1\n")
        with self.assertRaisesRegex(ValueError, r"\[Config\]"):
            config.load(self.path)

    def test_resolve_and_to_dict(self):
        cfg = config.Config()
        self.assertEqual(cfg.resolve("tasks/smoke"), config.REPO_ROOT / "tasks/smoke")
        self.assertEqual(cfg.resolve("/abs/path"), Path("/abs/path"))
        d = cfg.to_dict()
        self.assertEqual(d["model"]["name"], cfg.model.name)
        self.assertEqual(set(d), {"experiment", "model", "sandbox", "tasks", "limits"})


if __name__ == "__main__":
    unittest.main()
