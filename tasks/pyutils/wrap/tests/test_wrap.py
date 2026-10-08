import pytest

from solution import wrap


def test_greedy():
    assert wrap("the quick brown fox jumps over the lazy dog", 10) == ["the quick", "brown fox", "jumps over", "the lazy", "dog"]


def test_exact_fit():
    assert wrap("aaa bbb", 7) == ["aaa bbb"]


def test_collapse_whitespace():
    assert wrap("  a\tb \n c  ", 20) == ["a b c"]


def test_paragraphs():
    assert wrap("one two\n\n\n  \nthree", 20) == ["one two", "", "three"]


def test_long_word():
    assert wrap("ab abcdefghij cd", 4) == ["ab", "abcd", "efgh", "ij", "cd"]
    assert wrap("abcdefghij k", 4) == ["abcd", "efgh", "ij k"]


def test_justify():
    assert wrap("the quick brown fox jumps over the lazy dog", 16, justify=True) == [
        "the  quick brown",
        "fox  jumps  over",
        "the lazy dog",
    ]


def test_justify_single_word_and_paragraph_end():
    assert wrap("abcdefgh ij\n\nx y", 9, justify=True) == ["abcdefgh", "ij", "", "x y"]


def test_edge_cases():
    assert wrap("", 5) == []
    assert wrap("   \n\n  ", 5) == []
    with pytest.raises(ValueError):
        wrap("a", 0)
