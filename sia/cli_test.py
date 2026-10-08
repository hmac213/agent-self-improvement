import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from sia import cli, config


class ParseOverridesTest(unittest.TestCase):
    def test_json_values_and_raw_strings(self):
        out = cli._parse_overrides(["limits.task_llm_calls=5", "model.name=claude-x", "a.b=true", "a.c=[1, 2]", "a.d="])
        self.assertEqual(out, {"limits.task_llm_calls": 5, "model.name": "claude-x", "a.b": True, "a.c": [1, 2], "a.d": ""})


class RunManyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_runs_replicates_and_reports_failures(self):
        cfg = config.Config()
        cfg.experiment.name = "exp"
        seen = []

        def fake_supervisor(cfg_, run_dir, seed_harness=None, verbose=True):
            seen.append((run_dir.name, seed_harness))
            sup = mock.Mock()
            if run_dir.name.endswith("-r1"):
                sup.run.side_effect = RuntimeError("boom")
            else:
                sup.run.return_value = {"mean_score": 1.0}
            return sup

        with mock.patch.object(cli, "Supervisor", side_effect=fake_supervisor), redirect_stderr(io.StringIO()) as err:
            results = cli.run_many(cfg, self.root, 3, seeds=[Path("s0"), Path("s1")], tag="-g0")
        self.assertEqual(len(results), 3)
        self.assertTrue(all(d.parent == self.root and d.name.startswith("exp-g0-") for d, _ in results))
        self.assertEqual(results[0][1], {"mean_score": 1.0})
        self.assertEqual(results[1][1]["mean_score"], 0.0)
        self.assertIn("boom", results[1][1]["error"])
        self.assertIn("failed", err.getvalue())
        self.assertEqual(sorted(s for _, s in seen), [Path("s0"), Path("s0"), Path("s1")])


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg_path = self.root / "exp.toml"
        self.cfg_path.write_text('[experiment]\nname = "e"\nreplicates = 2\n')

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_command(self):
        with_events = self.root / "a"
        with_events.mkdir()
        (with_events / "events.jsonl").write_text("")
        results = [(with_events, {}), (self.root / "b", {"error": "x"})]
        with mock.patch.object(cli, "run_many", return_value=results) as rm, \
                mock.patch.object(cli.report, "analyze", return_value={"a": 1}), \
                mock.patch.object(cli.report, "render", return_value="RENDERED"), \
                redirect_stdout(io.StringIO()) as out:
            cli.main(["run", str(self.cfg_path), "--runs", str(self.root), "--set", "model.upstream=mock"])
        cfg, runs_root, n = rm.call_args[0]
        self.assertEqual((cfg.model.upstream, runs_root, n), ("mock", self.root, 2))
        self.assertIn("RENDERED", out.getvalue())
        self.assertIn("{'error': 'x'}", out.getvalue())

    def test_run_command_replicates_flag(self):
        with mock.patch.object(cli, "run_many", return_value=[]) as rm:
            cli.main(["run", str(self.cfg_path), "--replicates", "5"])
        self.assertEqual(rm.call_args[0][2], 5)

    def test_evolve_keeps_top_survivors(self):
        def fake_run_many(cfg, root, n, seeds, tag):
            out = []
            for i, score in enumerate([0.2, 0.9, 0.5]):
                d = root / f"run{tag}-{i}"
                if i != 2:  # run 2 produced no harness
                    (d / "final" / "agent_home" / "harness").mkdir(parents=True)
                    (d / "final" / "agent_home" / "harness" / "main.py").write_text(str(i))
                out.append((d, {"mean_score": score}))
            calls.append(seeds)
            return out

        calls = []
        with mock.patch.object(cli, "run_many", side_effect=fake_run_many), redirect_stdout(io.StringIO()) as out:
            cli.main(["evolve", str(self.cfg_path), "--rounds", "2", "--population", "3", "--survivors", "2", "--runs", str(self.root)])
        self.assertIsNone(calls[0])  # first round uses the configured seed
        self.assertEqual([p.name for p in calls[1]], ["g0-rank0"])  # rank1 (run 2) had no harness
        self.assertEqual((calls[1][0] / "main.py").read_text(), "1")
        lineage = json.loads(out.getvalue())
        self.assertEqual(len(lineage), 2)
        self.assertEqual([r["mean_score"] for r in lineage[0]["results"]], [0.9, 0.5, 0.2])
        evolve_dir = next(self.root.glob("evolve-e-*"))
        self.assertEqual(json.loads((evolve_dir / "lineage.json").read_text()), lineage)

    def test_report_and_compare(self):
        with mock.patch.object(cli.report, "analyze", return_value={"k": 1}), \
                mock.patch.object(cli.report, "render", return_value="TEXT"), \
                redirect_stdout(io.StringIO()) as out:
            cli.main(["report", "somedir"])
            cli.main(["report", "somedir", "--json"])
        self.assertIn("TEXT", out.getvalue())
        self.assertIn('"k": 1', out.getvalue())
        with mock.patch.object(cli.report, "compare", return_value="TABLE") as cmp, redirect_stdout(io.StringIO()) as out:
            cli.main(["compare", "a", "b"])
        self.assertEqual(cmp.call_args[0][0], [Path("a"), Path("b")])
        self.assertIn("TABLE", out.getvalue())

    def test_requires_subcommand(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main([])


if __name__ == "__main__":
    unittest.main()
