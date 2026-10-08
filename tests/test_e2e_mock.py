"""End-to-end runs with the scripted mock model (no API key needed)."""

import json
import shutil
import subprocess

import pytest

from sia import report
from sia.config import REPO_ROOT, load
from sia.supervisor import Supervisor

CFG = REPO_ROOT / "experiments" / "mock_smoke.toml"


def run(tmp_path, **overrides):
    cfg = load(CFG, overrides)
    run_dir = tmp_path / "run"
    summary = Supervisor(cfg, run_dir, verbose=False).run()
    return run_dir, summary, report.analyze(run_dir)


def calls(run_dir):
    return [json.loads(line) for line in (run_dir / "llm_calls.jsonl").read_text().splitlines()]


def test_self_modify_and_restart(tmp_path):
    run_dir, summary, a = run(tmp_path)
    assert summary["mean_score"] == 1.0 and summary["tasks_graded"] == 3
    assert a["signals"]["harness_modified"] and a["signals"]["voluntary_restarts"] == 1
    assert a["restarts"]["sigterm(143)"] == 1
    # The relaunched harness loaded the tool the agent wrote.
    tools_by_gen = {c["generation"]: [t["name"] for t in c["request"]["tools"]] for c in calls(run_dir)}
    assert "note" not in tools_by_gen[0] and "note" in tools_by_gen[1]
    assert (run_dir / "generations" / "gen_001" / "diff_vs_prev.patch").read_text().count("+SPEC") == 1
    # Resumed the same conversation: the interrupted tool call got a result.
    first_gen1 = next(c for c in calls(run_dir) if c["generation"] == 1)
    assert "Interrupted" in json.dumps(first_gen1["request"]["messages"][-1])


def test_restart_tool_at_affordance_2(tmp_path):
    run_dir, summary, a = run(tmp_path, **{"experiment.affordance": 2})
    assert summary["mean_score"] == 1.0
    assert a["restarts"]["restart_tool(75)"] == 1
    system = calls(run_dir)[0]["request"]["system"]
    assert "harness" in system and "restart" in system


def test_affordance_0_prompt_is_silent_about_harness(tmp_path):
    run_dir, _, _ = run(tmp_path, **{"model.mock_policy": "solve"})
    first = calls(run_dir)[0]["request"]
    assert "harness" not in first["system"]
    assert "restart" not in [t["name"] for t in first["tools"]]


def test_rollback_after_broken_harness(tmp_path):
    run_dir, summary, a = run(tmp_path, **{"model.mock_policy": "break_harness"})
    assert summary["mean_score"] == 1.0
    assert a["rollbacks"] == 1
    gens = a["generations"]
    assert [g["changed_by"] for g in gens].count("rollback") == 1
    resumed = [c for c in calls(run_dir) if c["generation"] == gens[-1]["generation"]][0]
    assert "failed to start" in json.dumps(resumed["request"]["messages"][-1])


def test_task_call_budget_is_exact(tmp_path):
    # The mock answers instantly, far faster than the supervisor polls; the
    # proxy must still stop each task at exactly its budget.
    run_dir, summary, a = run(tmp_path, **{"model.mock_policy": "solve", "tasks.suite": "tasks/pyutils",
                                            "limits.task_llm_calls": 3})
    results = [json.loads(l) for l in (run_dir / "results.jsonl").read_text().splitlines()]
    assert len(results) == 8 and summary["mean_score"] == 0.0
    assert all(r["reason"] == "task_call_budget" and r["llm_calls"] == 3 for r in results)
    assert a["rollbacks"] == 0 and len(a["generations"]) == 1


def _docker_ready():
    if not shutil.which("docker"):
        return False
    p = subprocess.run(["docker", "image", "inspect", "sia-sandbox:latest"], capture_output=True)
    return p.returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="docker or sia-sandbox:latest image not available")
def test_docker_sandbox(tmp_path):
    run_dir, summary, a = run(tmp_path, **{"sandbox.kind": "docker"})
    assert summary["mean_score"] == 1.0 and a["signals"]["harness_modified"]


def test_inherited_harness_installed_verbatim(tmp_path):
    parent_dir, _, _ = run(tmp_path / "parent")
    parent = parent_dir / "final" / "agent_home" / "harness"
    (parent / "system_prompt.md").write_text("Edited by the parent agent.\n")
    cfg = load(CFG, {"model.mock_policy": "solve"})
    child_dir = tmp_path / "child"
    Supervisor(cfg, child_dir, seed_harness=parent, verbose=False).run()
    first = calls(child_dir)[0]["request"]
    assert first["system"] == "Edited by the parent agent.\n"
    assert "note" in [t["name"] for t in first["tools"]]
