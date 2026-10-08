from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        if capacity < 0:
            raise ValueError(capacity)
        self.capacity = capacity
        self._d = OrderedDict()
        self._stats = {"hits": 0, "misses": 0, "evictions": 0}

    def get(self, key, default=None):
        if key in self._d:
            self._d.move_to_end(key)
            self._stats["hits"] += 1
            return self._d[key]
        self._stats["misses"] += 1
        return default

    def put(self, key, value):
        if self.capacity == 0:
            return
        self._d[key] = value
        self._d.move_to_end(key)
        self._evict()

    def _evict(self):
        while len(self._d) > self.capacity:
            self._d.popitem(last=False)
            self._stats["evictions"] += 1

    def resize(self, capacity):
        if capacity < 0:
            raise ValueError(capacity)
        self.capacity = capacity
        self._evict()

    def __len__(self):
        return len(self._d)

    def __contains__(self, key):
        return key in self._d

    def keys(self):
        return list(self._d)

    def stats(self):
        return dict(self._stats)
