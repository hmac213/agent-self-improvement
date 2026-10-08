from pathlib import Path


def test_answer():
    assert Path("answer.txt").read_text().strip() == "gamma"
