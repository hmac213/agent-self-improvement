"""Task suites and grading.

A suite is a directory:

    <suite>/suite.toml          name, description, optional `order = [...]`
    <suite>/<task_id>/prompt.md task statement shown to the agent
    <suite>/<task_id>/starter/  files copied into the task's working directory (optional)
    <suite>/<task_id>/tests/    hidden pytest tests, never visible to the agent
    <suite>/<task_id>/reference/ reference solution, host-only (used to validate tests)

Grading copies the agent's working directory to a scratch location (in a fresh
grading sandbox when the backend provides one), adds the
hidden tests, runs pytest and counts passing tests from the JUnit report.
"""

from __future__ import annotations

import random
import shlex
import tomllib
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .sandbox import Sandbox


@dataclass
class Task:
    id: str
    dir: Path
    prompt: str

    @property
    def title(self) -> str:
        first = self.prompt.strip().splitlines()[0]
        return first.lstrip("# ").strip()


@dataclass
class Grade:
    task_id: str
    passed: int
    total: int
    failures: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0


def load_suite(path: Path) -> list[Task]:
    meta = tomllib.loads((path / "suite.toml").read_text()) if (path / "suite.toml").exists() else {}
    ids = meta.get("order") or sorted(p.name for p in path.iterdir() if (p / "prompt.md").exists())
    return [Task(id=i, dir=path / i, prompt=(path / i / "prompt.md").read_text()) for i in ids]


def schedule(tasks: list[Task], order: str, epochs: int, seed: int) -> list[tuple[str, Task]]:
    """Return [(instance_id, task)] for the whole run."""
    rng = random.Random(seed)
    out = []
    for epoch in range(epochs):
        batch = list(tasks)
        if order == "shuffled":
            rng.shuffle(batch)
        for t in batch:
            out.append((t.id if epochs == 1 else f"{t.id}-e{epoch}", t))
    return out


def prepare_workdir(sb: Sandbox, task: Task, workdir: str) -> None:
    sb.check(f"mkdir -p {shlex.quote(workdir)}")
    if (task.dir / "starter").is_dir():
        sb.upload_dir(task.dir / "starter", workdir)
    sb.give_to_agent(workdir)


def grade(sb: Sandbox, task: Task, workdir: str, timeout: float = 300) -> Grade:
    """Grade the contents of `workdir` with the task's hidden tests.

    If the backend offers a grading sandbox (docker), the working directory is
    copied into a fresh one and the hidden tests only ever exist there, so
    nothing running in the agent's sandbox can read them. Otherwise grading
    happens in a scratch directory of the agent's own sandbox."""
    grader = sb.grading_sandbox()
    if grader is None:
        return _grade_in(sb, task, workdir, timeout)
    files = sb.pack_dir(workdir)
    grader.setup()
    try:
        if files is None:
            grader.check(f"mkdir -p {shlex.quote(workdir)}")
        else:
            grader.unpack_dir(files, workdir)
        return _grade_in(grader, task, workdir, timeout)
    finally:
        grader.teardown()


def _grade_in(sb: Sandbox, task: Task, workdir: str, timeout: float) -> Grade:
    scratch = f"{sb.paths.runtime}/grade-{uuid.uuid4().hex[:8]}"
    q = shlex.quote(scratch)
    try:
        sb.check(f"mkdir -p {q} && cp -a {shlex.quote(workdir)}/. {q}/ && rm -rf {q}/_hidden_tests")
        sb.upload_dir(task.dir / "tests", f"{scratch}/_hidden_tests")
        code, out = sb.exec(
            f"cd {q} && timeout {int(timeout)} {sb.python} -m pytest -q -p no:cacheprovider "
            f"--junitxml={q}/_report.xml _hidden_tests 2>&1 | tail -40",
            timeout=timeout + 30,
        )
        xml = sb.read_bytes(f"{scratch}/_report.xml")
        if xml is None:
            return Grade(task.id, 0, _count_tests(task), error=f"no test report; pytest output:\n{out[-2000:]}")
        return _parse_junit(task.id, xml, _count_tests(task))
    finally:
        sb.exec(f"rm -rf {q}")


def _count_tests(task: Task) -> int:
    n = 0
    for f in (task.dir / "tests").glob("test_*.py"):
        n += sum(1 for line in f.read_text().splitlines() if line.lstrip().startswith("def test_"))
    return max(n, 1)


def _parse_junit(task_id: str, xml: bytes, expected: int) -> Grade:
    root = ET.fromstring(xml)
    passed, total, failures = 0, 0, []
    for case in root.iter("testcase"):
        total += 1
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is None and case.find("skipped") is None:
            passed += 1
        elif bad is not None:
            failures.append({"test": case.get("name"), "message": (bad.get("message") or "")[:300]})
    # A module that fails to import collapses into a single error case; count
    # against the number of tests the suite defines.
    error = None if total else "no tests collected"
    return Grade(task_id, passed, max(total, expected), failures, error)
