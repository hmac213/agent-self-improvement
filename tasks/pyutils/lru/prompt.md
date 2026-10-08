# lru

Implement `class LRUCache` in `solution.py`:

- `LRUCache(capacity: int)`; raise `ValueError` if capacity is negative. A capacity of 0 stores nothing.
- `get(key, default=None)` returns the value and marks the key most recently used; returns `default` if absent.
- `put(key, value)` inserts or updates and marks the key most recently used, evicting the least recently used entry if over capacity.
- `__len__`, `__contains__` (does **not** change recency), `keys()` returning a list from least to most recently used.
- `stats()` returning a dict `{"hits": int, "misses": int, "evictions": int}` counting `get` hits/misses and evictions.
- `resize(capacity)` changes capacity, evicting least recently used entries as needed (they count as evictions).
- Both `get` and `put` must be O(1) on average.
