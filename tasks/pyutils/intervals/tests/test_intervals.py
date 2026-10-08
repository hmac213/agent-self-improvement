import pytest

from solution import insert, merge, subtract, total_length


def test_merge_basic():
    assert merge([(1, 3), (2, 6), (8, 10), (15, 18)]) == [(1, 6), (8, 10), (15, 18)]


def test_merge_touching_vs_adjacent():
    assert merge([(1, 3), (3, 5)]) == [(1, 5)]
    assert merge([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]


def test_merge_unsorted_nested():
    assert merge([(5, 6), (1, 10), (2, 3)]) == [(1, 10)]


def test_merge_empty_and_invalid():
    assert merge([]) == []
    with pytest.raises(ValueError):
        merge([(3, 1)])


def test_merge_does_not_mutate():
    data = [(5, 6), (1, 2)]
    merge(data)
    assert data == [(5, 6), (1, 2)]


def test_insert():
    assert insert([(1, 2), (3, 5), (6, 7), (8, 10), (12, 16)], (4, 8)) == [(1, 2), (3, 10), (12, 16)]
    assert insert([], (1, 1)) == [(1, 1)]


def test_subtract():
    assert subtract([(1, 10)], (3, 5)) == [(1, 2), (6, 10)]
    assert subtract([(1, 3), (5, 8)], (2, 6)) == [(1, 1), (7, 8)]
    assert subtract([(1, 3)], (0, 10)) == []
    assert subtract([(1, 3)], (5, 6)) == [(1, 3)]


def test_total_length():
    assert total_length([(1, 3), (2, 4)]) == 4
    assert total_length([(1, 1), (3, 3)]) == 2
    assert total_length([]) == 0
