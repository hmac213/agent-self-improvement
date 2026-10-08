"""Command line entry point: `python -m sia <command>`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as config_mod
from . import report
from .config import REPO_ROOT
from .supervisor import Supervisor


def _parse_overrides(items: list[str]) -> dict:
    out = {}
    for item in items:
        key, _, raw = item.partition("=")
        try:
            out[key] = json.loads(raw)
        except json.JSONDecodeError:
            out[key] = raw
    return out


def run_many(cfg, runs_root: Path, n: int, seeds: list[Path] | None = None, tag: str = "") -> list[tuple[Path, dict]]:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dirs = [runs_root / f"{cfg.experiment.name}{tag}-{stamp}-r{i}" for i in range(n)]

    def one(i: int):
        seed = seeds[i % len(seeds)] if seeds else None
        try:
            return dirs[i], Supervisor(cfg, dirs[i], seed_harness=seed, verbose=True).run()
        except Exception as e:
            print(f"run {dirs[i].name} failed: {e!r}", file=sys.stderr)
            return dirs[i], {"mean_score": 0.0, "error": repr(e)}

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one, range(n)))


def cmd_run(args) -> None:
    cfg = config_mod.load(args.config, _parse_overrides(args.set))
    n = args.replicates or cfg.experiment.replicates
    results = run_many(cfg, Path(args.runs), n)
    for d, summary in results:
        print(report.render(report.analyze(d)) if (d / "events.jsonl").exists() else f"{d}: {summary}")


def cmd_evolve(args) -> None:
    """Population loop: run N agents, keep the top-K final harnesses as seeds for the next round."""
    cfg = config_mod.load(args.config, _parse_overrides(args.set))
    root = Path(args.runs) / f"evolve-{cfg.experiment.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    pool_dir = root / "pool"
    seeds: list[Path] | None = None  # None = the configured seed, rendered for this experiment
    lineage = []
    for round_ in range(args.rounds):
        results = run_many(cfg, root, args.population, seeds, tag=f"-g{round_}")
        ranked = sorted(results, key=lambda r: r[1].get("mean_score", 0.0), reverse=True)
        seeds = []
        for rank, (d, summary) in enumerate(ranked[: args.survivors]):
            src = d / "final" / "agent_home" / "harness"
            if not src.exists():
                continue
            dest = pool_dir / f"g{round_}-rank{rank}"
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
            seeds.append(dest)
        lineage.append({"round": round_, "results": [{"run": d.name, "mean_score": s.get("mean_score")} for d, s in ranked], "survivors": [str(s) for s in seeds]})
        (root / "lineage.json").write_text(json.dumps(lineage, indent=2))
        seeds = seeds or None
    print(json.dumps(lineage, indent=2))


def cmd_report(args) -> None:
    a = report.analyze(Path(args.run_dir))
    print(json.dumps(a, indent=2) if args.json else report.render(a))


def cmd_compare(args) -> None:
    print(report.compare([Path(d) for d in args.run_dirs]))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sia", description="Self-improvement arena for coding agents")
    sub = p.add_subparsers(required=True)

    r = sub.add_parser("run", help="run an experiment")
    r.add_argument("config")
    r.add_argument("--replicates", type=int)
    r.add_argument("--runs", default=str(REPO_ROOT / "runs"))
    r.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE", help="override a config value")
    r.set_defaults(fn=cmd_run)

    e = sub.add_parser("evolve", help="population loop with selection on task score")
    e.add_argument("config")
    e.add_argument("--rounds", type=int, default=3)
    e.add_argument("--population", type=int, default=4)
    e.add_argument("--survivors", type=int, default=2)
    e.add_argument("--runs", default=str(REPO_ROOT / "runs"))
    e.add_argument("--set", action="append", default=[], metavar="SECTION.KEY=VALUE")
    e.set_defaults(fn=cmd_evolve)

    rp = sub.add_parser("report", help="summarise one run")
    rp.add_argument("run_dir")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(fn=cmd_report)

    c = sub.add_parser("compare", help="one row per run")
    c.add_argument("run_dirs", nargs="+")
    c.set_defaults(fn=cmd_compare)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
