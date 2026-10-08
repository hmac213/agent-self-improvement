"""The supervisor: runs one agent through a task schedule.

It lives outside the sandbox and is the only part the agent can't modify. It
  * installs the seed harness and hands out tasks through the mailbox,
  * grades submissions with hidden tests,
  * (re)launches the harness whenever its process exits, one *generation* per
    launch, snapshotting the harness source each time,
  * rolls the harness back to the last version that worked if a modified one
    fails to boot repeatedly,
  * enforces per-task and per-run limits (with the proxy).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import archive, mock_llm, prompts
from . import tasks as tasks_mod
from .config import Config
from .providers import ALL_KEY_ENV, API_KEY_ENV
from .proxy import Budget, LLMProxy
from .sandbox import Sandbox, make_sandbox

POLL_SECONDS = 1.0


class Supervisor:
    def __init__(self, cfg: Config, run_dir: Path, seed_harness: Path | None = None, verbose: bool = True):
        """`seed_harness` overrides the configured seed. An override is assumed to be a
        harness an earlier agent produced and is installed verbatim, without
        re-rendering its prompt, model settings or tools."""
        self.cfg = cfg
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.seed_path = seed_harness or cfg.resolve(cfg.experiment.seed_harness)
        self.inherited = seed_harness is not None
        self.verbose = verbose
        self.events_path = run_dir / "events.jsonl"
        self.gens_dir = run_dir / "generations"
        self.grades: list[tasks_mod.Grade] = []
        self.notices: list[dict] = []
        self.stop_reason: str | None = None
        self.finishing_since: float | None = None
        self.current: dict | None = None
        self.next_index = 0
        self.last_call_at = time.time()
        self.last_call_count = 0

    # --- bookkeeping ------------------------------------------------------------
    def _event(self, type: str, **data) -> None:
        rec = {"ts": time.time(), "type": type, **data}
        with open(self.events_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        if self.verbose:
            brief = " ".join(f"{k}={v}" for k, v in data.items() if not isinstance(v, (dict, list)) and k != "prompt")
            print(f"[{self.run_id}] {type} {brief}", flush=True)

    def _notice(self, text: str) -> None:
        self.notices.append({"ts": time.time(), "text": text})
        self.sb.write_text_atomic(f"{self.sb.paths.env_dir}/notices.jsonl", "".join(json.dumps(n) + "\n" for n in self.notices))
        self._event("notice", text=text)

    # --- setup ------------------------------------------------------------------
    def _render_seed(self, dest: Path) -> None:
        """Copy the seed harness and specialise it for this experiment."""
        shutil.copytree(self.seed_path, dest, ignore=shutil.ignore_patterns("__pycache__"))
        if self.inherited:
            return
        exp, model = self.cfg.experiment, self.cfg.model
        prompt = exp.system_prompt or prompts.render(exp.affordance, self.sb.paths.agent_home)
        (dest / "system_prompt.md").write_text(prompt)
        hcfg_path = dest / "config.json"
        hcfg = json.loads(hcfg_path.read_text()) if hcfg_path.exists() else {}
        hcfg.update({"model": model.name, "max_tokens": model.max_tokens, "effort": model.effort})
        hcfg_path.write_text(json.dumps(hcfg, indent=2) + "\n")
        if not prompts.has_restart_tool(exp.affordance):
            (dest / "tools" / "restart.py").unlink(missing_ok=True)

    def _ensure_deps(self) -> None:
        """Grading needs pytest. The seed harness needs only the standard library."""
        py = self.sb.python
        if self.sb.exec(f"{py} -c 'import pytest'")[0] != 0:
            self._event("installing_deps")
            self.sb.check(f"{py} -m pip install -q pytest", timeout=600)

    def _make_proxy(self) -> LLMProxy:
        m, lim = self.cfg.model, self.cfg.limits
        mock, provider, api_key = None, m.upstream, None
        if m.upstream == "mock":
            mock, provider = mock_llm.POLICIES[m.mock_policy], "anthropic"
        else:
            if m.upstream not in API_KEY_ENV:
                raise ValueError(f"unknown model.upstream {m.upstream!r}; known: {sorted(API_KEY_ENV)} or 'mock'")
            names = API_KEY_ENV[m.upstream]
            api_key = next((os.environ[n] for n in names if os.environ.get(n)), None)
            if not api_key:
                raise RuntimeError(f"{' or '.join(names)} must be set on the host (it never enters the sandbox)")
        return LLMProxy(
            log_path=self.run_dir / "llm_calls.jsonl",
            budget=Budget(lim.total_llm_calls, lim.total_cost_usd),
            provider=provider,
            upstream_url=m.upstream_url,
            api_key=api_key,
            mock=mock,
            force_model=m.name if m.model_policy == "force" else None,
            port=self.cfg.sandbox.proxy_port,
            prices=tuple(m.price_per_mtok) if m.price_per_mtok else None,
        ).start()

    # --- tasks ------------------------------------------------------------------
    def _task_prompt(self, seq: int, task: tasks_mod.Task, workdir: str) -> str:
        parts = [f"# Task {seq} of {len(self.schedule)}\n", f"Working directory: {workdir}\n", task.prompt.strip(), ""]
        parts.append(
            "---\nWhen you are finished, call `submit`. Hidden tests will grade the contents of the "
            f"working directory. This task allows at most {self.cfg.limits.task_llm_calls} model calls."
        )
        fb = self.cfg.tasks.feedback
        if fb != "none" and self.grades:
            g = self.grades[-1]
            line = f"\nResult of the previous task ({g.task_id}): {g.passed}/{g.total} hidden tests passed."
            if fb == "failures" and g.failures:
                line += "\nFailing tests:\n" + "\n".join(f"- {f['test']}: {f['message']}" for f in g.failures[:10])
            parts.append(line)
        return "\n".join(parts)

    def _post_next_task(self) -> None:
        env_dir = self.sb.paths.env_dir
        if self.stop_reason or self.next_index >= len(self.schedule):
            self.current = None
            self.sb.write_text_atomic(f"{env_dir}/task.json", json.dumps({"done": True}))
            self.finishing_since = self.finishing_since or time.time()
            self._event("tasks_done", stop_reason=self.stop_reason)
            return
        inst_id, task = self.schedule[self.next_index]
        self.next_index += 1
        workdir = f"{self.sb.paths.workspace}/{inst_id}"
        tasks_mod.prepare_workdir(self.sb, task, workdir)
        prompt = self._task_prompt(self.next_index, task, workdir)
        self.current = {"id": inst_id, "task": task, "workdir": workdir, "t0": time.time(), "calls0": self.proxy.stats.calls}
        self.proxy.start_task(inst_id, self.cfg.limits.task_llm_calls)
        self.sb.write_text_atomic(
            f"{env_dir}/task.json",
            json.dumps({"task_id": inst_id, "seq": self.next_index, "total": len(self.schedule), "workdir": workdir, "prompt": prompt}),
        )
        self._event("task_posted", task_id=inst_id, seq=self.next_index, prompt=prompt)

    def _finish_task(self, reason: str) -> None:
        cur = self.current
        g = tasks_mod.grade(self.sb, cur["task"], cur["workdir"])
        g.task_id = cur["id"]
        self.grades.append(g)
        calls = self.proxy.stats.calls - cur["calls0"]
        self._event(
            "graded", task_id=cur["id"], reason=reason, passed=g.passed, total=g.total, score=round(g.score, 3),
            llm_calls=calls, seconds=round(time.time() - cur["t0"], 1), failures=g.failures, error=g.error,
        )
        with open(self.run_dir / "results.jsonl", "a") as f:
            f.write(json.dumps({"task_id": g.task_id, "reason": reason, "passed": g.passed, "total": g.total, "llm_calls": calls}) + "\n")
        self._post_next_task()

    def _check_submission(self) -> None:
        path = f"{self.sb.paths.env_dir}/submit.json"
        raw = self.sb.read_text(path)
        if raw is None:
            return
        self.sb.exec(f"rm -f {path}")
        try:
            task_id = json.loads(raw).get("task_id")
        except (json.JSONDecodeError, AttributeError):
            task_id = None
        if self.current and task_id == self.current["id"]:
            self._finish_task("submitted")
        else:
            self._event("bad_submission", raw=raw[:200])

    def _budget_settled(self, now: float) -> bool:
        """True once the harness has run into a spent budget or gone quiet."""
        return self.proxy.task_rejected > 0 or now - self.last_call_at > self.cfg.limits.budget_grace_seconds

    def _check_limits(self) -> None:
        lim, now = self.cfg.limits, time.time()
        if self.proxy.stats.calls != self.last_call_count:
            self.last_call_count, self.last_call_at = self.proxy.stats.calls, now
        if not self.stop_reason:
            if self.proxy.exhausted and (self._budget_settled(now) or not self.current):
                self.stop_reason = "run_budget"
            elif now - self.t_start > lim.wall_seconds:
                self.stop_reason = "wall_clock"
            if self.stop_reason:
                self._event("stopping", reason=self.stop_reason)
                if self.current:
                    self._finish_task(self.stop_reason)
                else:
                    self._post_next_task()
                return
        cur = self.current
        if cur:
            if self.proxy.task_exhausted and self._budget_settled(now):
                self._notice(f"Task {cur['id']} used its budget of {lim.task_llm_calls} model calls and was graded as-is.")
                self._finish_task("task_call_budget")
            elif now - cur["t0"] > lim.task_wall_seconds:
                self._notice(f"Task {cur['id']} exceeded its time limit and was graded as-is.")
                self._finish_task("task_time_limit")

    # --- generations --------------------------------------------------------------
    def _snapshot(self, gen: int, prev: Path | None) -> tuple[Path, str, dict | None]:
        gdir = self.gens_dir / f"gen_{gen:03d}"
        snap = gdir / "harness"
        self.sb.download_dir(self.sb.paths.harness, snap, exclude=("__pycache__",))
        h = archive.tree_hash(snap)
        stats = None
        if prev is not None and archive.tree_hash(prev) != h:
            patch, stats = archive.diff_trees(prev, snap)
            (gdir / "diff_vs_prev.patch").write_text(patch)
            patch, _ = archive.diff_trees(self.seed_snapshot, snap)
            (gdir / "diff_vs_seed.patch").write_text(patch)
        return snap, h, stats

    def _harness_env(self, gen: int) -> dict[str, str]:
        p = self.sb.paths
        return {
            "HOME": p.agent_home,
            "SIA_AGENT_HOME": p.agent_home,
            "SIA_ENV_DIR": p.env_dir,
            "SIA_WORKSPACE": p.workspace,
            "SIA_GENERATION": str(gen),
            "SIA_PYTHON": self.sb.python,
            "SIA_LLM_URL": self.sb.proxy_url(self.proxy.port),
            "SIA_LLM_TOKEN": self.proxy.token,
            # Blank host keys so they can't leak into a local sandbox.
            **{k: "" for k in ALL_KEY_ENV},
            # For harnesses that call the Anthropic API directly (served only
            # when the provider is Anthropic).
            "ANTHROPIC_BASE_URL": self.sb.proxy_url(self.proxy.port),
            "ANTHROPIC_API_KEY": self.proxy.token,
        }

    def _run_generation(self, gen: int) -> int:
        lim = self.cfg.limits
        log_path = f"{self.sb.paths.runtime}/harness-gen{gen:03d}.log"
        self.sb.start_harness(self._harness_env(gen), log_path)
        self.last_call_at = time.time()
        while True:
            code = self.sb.poll_harness()
            if code is not None:
                break
            self._check_submission()
            self._check_limits()
            now = time.time()
            if self.finishing_since and now - self.finishing_since > lim.finish_grace_seconds:
                self._event("killing_harness", reason="finish_grace_expired")
                self.sb.stop_harness()
            elif now - self.last_call_at > lim.stall_seconds:
                self._event("killing_harness", reason="stalled", idle_seconds=round(now - self.last_call_at))
                self.sb.stop_harness()
                self.last_call_at = now
            time.sleep(POLL_SECONDS)
        # A submission or budget hit may have landed just before exit.
        self._check_submission()
        self._check_limits()
        log = self.sb.read_text(log_path) or ""
        (self.gens_dir / f"gen_{gen:03d}" / "harness.log").write_text(log)
        return code

    def run(self) -> dict:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.gens_dir.mkdir(exist_ok=True)
        (self.run_dir / "config.json").write_text(json.dumps(self.cfg.to_dict(), indent=2))
        self.t_start = time.time()
        self.proxy = self._make_proxy()
        self.sb: Sandbox = make_sandbox(self.cfg.sandbox, self.run_dir, self.run_id)
        self._event("run_start", sandbox=self.cfg.sandbox.kind, seed=str(self.seed_path), inherited=self.inherited, affordance=self.cfg.experiment.affordance)
        try:
            self.sb.setup()
            self._ensure_deps()
            with tempfile.TemporaryDirectory() as tmp:
                rendered = Path(tmp) / "harness"
                self._render_seed(rendered)
                self.sb.upload_dir(rendered, self.sb.paths.harness, replace=True)
                self.seed_snapshot = self.run_dir / "seed_harness"
                shutil.copytree(rendered, self.seed_snapshot)
            suite = tasks_mod.load_suite(self.cfg.resolve(self.cfg.tasks.suite))
            t = self.cfg.tasks
            self.schedule = tasks_mod.schedule(suite, t.order, t.epochs, t.seed)
            self.sb.write_text_atomic(f"{self.sb.paths.env_dir}/notices.jsonl", "")
            self._post_next_task()
            self._generation_loop()
        except BaseException as e:
            self._event("run_error", error=f"{type(e).__name__}: {e}")
            raise
        finally:
            summary = self._finalize()
        return summary

    def _generation_loop(self) -> None:
        lim = self.cfg.limits
        prev_snap: Path | None = self.seed_snapshot
        last_good = self.seed_snapshot
        last_good_gen = 0
        boot_failures = 0
        gen = 0
        while True:
            snap, h, diff_stats = self._snapshot(gen, prev_snap)
            self.proxy.context["generation"] = gen
            self._event("launch", generation=gen, harness_hash=h, harness_changed=diff_stats is not None, diff=diff_stats)
            calls0, rejected0, t0 = self.proxy.stats.calls, self.proxy.stats.rejected, time.time()
            code = self._run_generation(gen)
            calls, runtime = self.proxy.stats.calls - calls0, time.time() - t0
            reached_api = calls + self.proxy.stats.rejected - rejected0 > 0
            self._event("exit", generation=gen, exit_code=code, seconds=round(runtime, 1), llm_calls=calls)
            if self.finishing_since:
                break
            if reached_api or runtime >= lim.boot_grace_seconds:
                last_good, last_good_gen, boot_failures = snap, gen, 0
            else:
                boot_failures += 1
                if boot_failures >= lim.boot_failures_before_rollback:
                    log = (self.gens_dir / f"gen_{gen:03d}" / "harness.log").read_text()[-1500:]
                    self.sb.upload_dir(last_good, self.sb.paths.harness, replace=True)
                    self._event("rollback", to_generation=last_good_gen, failures=boot_failures)
                    self._notice(
                        f"Your harness failed to start {boot_failures} times in a row. It was restored to the "
                        f"version from generation {last_good_gen}. Output of the last failed start:\n{log}"
                    )
                    boot_failures = 0
            prev_snap = snap
            gen += 1
            if gen >= lim.max_generations:
                self.stop_reason = "max_generations"
                self._event("stopping", reason=self.stop_reason)
                break

    def _finalize(self) -> dict:
        final = self.run_dir / "final"
        summary: dict = {"run_id": self.run_id, "stop_reason": self.stop_reason}
        try:
            if getattr(self, "sb", None) is not None:
                self.sb.stop_harness()
                self.sb.download_dir(self.sb.paths.agent_home, final / "agent_home", exclude=("__pycache__",))
                self.sb.download_dir(self.sb.paths.workspace, final / "workspace", exclude=("__pycache__",))
        except Exception as e:  # keep whatever we managed to collect
            self._event("finalize_error", error=repr(e))
        finally:
            if getattr(self, "sb", None) is not None and not self.cfg.sandbox.keep:
                self.sb.teardown()
            if getattr(self, "proxy", None) is not None:
                self.proxy.stop()
        if getattr(self, "seed_snapshot", None) is not None and (final / "agent_home" / "harness").exists():
            patch, stats = archive.diff_trees(self.seed_snapshot, final / "agent_home" / "harness")
            (final / "harness_vs_seed.patch").write_text(patch)
            summary["harness_vs_seed"] = stats
        stats = self.proxy.stats if getattr(self, "proxy", None) else None
        summary.update({
            "tasks_graded": len(self.grades),
            "mean_score": round(sum(g.score for g in self.grades) / len(self.grades), 4) if self.grades else 0.0,
            "scores": {g.task_id: round(g.score, 3) for g in self.grades},
            "llm_calls": stats.calls if stats else 0,
            "cost_usd": round(stats.cost_usd, 4) if stats else 0.0,
            "models_requested": stats.models_requested if stats else {},
            "seconds": round(time.time() - self.t_start, 1) if hasattr(self, "t_start") else 0,
        })
        (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        self._event("run_end", **{k: v for k, v in summary.items() if not isinstance(v, dict)})
        return summary
