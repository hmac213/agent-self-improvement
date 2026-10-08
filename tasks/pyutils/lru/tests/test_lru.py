import time

import pytest

from solution import LRUCache


def test_basic_eviction():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1
    c.put("c", 3)
    assert "b" not in c and c.keys() == ["a", "c"]


def test_update_moves_to_end():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)
    c.put("c", 3)
    assert c.keys() == ["a", "c"] and c.get("a") == 10


def test_contains_does_not_touch():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert "a" in c
    c.put("c", 3)
    assert "a" not in c


def test_default_and_stats():
    c = LRUCache(1)
    assert c.get("x", 5) == 5
    c.put("x", 1)
    c.get("x")
    c.put("y", 2)
    assert c.stats() == {"hits": 1, "misses": 1, "evictions": 1}


def test_zero_and_negative_capacity():
    c = LRUCache(0)
    c.put("a", 1)
    assert len(c) == 0 and c.get("a") is None
    with pytest.raises(ValueError):
        LRUCache(-1)


def test_resize():
    c = LRUCache(3)
    for k in "abc":
        c.put(k, k)
    c.resize(1)
    assert c.keys() == ["c"] and c.stats()["evictions"] == 2


def test_none_values():
    c = LRUCache(2)
    c.put("a", None)
    assert "a" in c and c.get("a", 7) is None


def test_performance():
    c = LRUCache(50_000)
    t = time.perf_counter()
    for i in range(200_000):
        c.put(i, i)
        c.get(i - 1000)
    assert time.perf_counter() - t < 5.0
