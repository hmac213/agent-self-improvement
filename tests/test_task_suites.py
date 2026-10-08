"""Hidden tests must accept the reference solution and reject the starter."""

import shutil
from pathlib import Path

import pytest

from sia import tasks
from sia.config import REPO_ROOT
from sia.sandbox.local import LocalSandbox

SUITE = tasks.load_suite(REPO_ROOT / "tasks" / "pyutils")


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    sb = LocalSandbox(tmp_path_factory.mktemp("sb"))
    sb.setup()
    return sb


def _grade(sb, task, source: Path, name: str):
    workdir = Path(sb.paths.workspace) / f"{task.id}-{name}"
    shutil.copytree(source, workdir)
    return tasks.grade(sb, task, str(workdir))


@pytest.mark.parametrize("task", SUITE, ids=lambda t: t.id)
def test_reference_passes(sandbox, task):
    g = _grade(sandbox, task, task.dir / "reference", "ref")
    assert g.passed == g.total and g.total > 0, g


@pytest.mark.parametrize("task", SUITE, ids=lambda t: t.id)
def test_starter_fails(sandbox, task):
    g = _grade(sandbox, task, task.dir / "starter", "starter")
    assert g.passed == 0, g
