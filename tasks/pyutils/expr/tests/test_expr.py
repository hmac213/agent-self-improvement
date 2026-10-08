import ast
import math
from pathlib import Path

import pytest

from solution import evaluate


@pytest.mark.parametrize("e,v", [
    ("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("10 / 4", 2.5), ("7 % 4", 3), ("2 ** 3 ** 2", 512),
    ("-2 ** 2", -4), ("2 ** -1", 0.5), ("--3", 3), ("-(2 + 3) * 2", -10), ("1 - 2 - 3", -4),
    (".5 + 1e3", 1000.5), ("2.5E-2 * 4", 0.1), ("  3  ", 3),
])
def test_arithmetic(e, v):
    assert math.isclose(evaluate(e), v)


def test_returns_float():
    assert isinstance(evaluate("1 + 1"), float)


def test_variables_and_functions():
    assert math.isclose(evaluate("max(1, x, 3) + abs(-y)", {"x": 10, "y": 2}), 12)
    assert math.isclose(evaluate("sqrt(16) + min(4, 2 * 3)"), 8)


@pytest.mark.parametrize("e", ["1 +", "(1 + 2", "1 2", "1 / 0", "5 % 0", "foo + 1", "bar(1)", "sqrt(1, 2)",
                               "max()", "3 $ 4", "", "1 + * 2"])
def test_errors(e):
    with pytest.raises(ValueError):
        evaluate(e)


def test_no_eval():
    assert evaluate("1 + 1") == 2
    tree = ast.parse(Path("solution.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not names & {"eval", "exec", "compile", "__builtins__", "builtins"}
    assert not imports & {"ast", "builtins"}
