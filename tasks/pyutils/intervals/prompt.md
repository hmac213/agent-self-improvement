# intervals

Implement in `solution.py`, for closed integer intervals given as `(start, end)` tuples with `start <= end`:

- `merge(intervals) -> list[tuple[int, int]]`: merge overlapping intervals and return them sorted by start. Intervals that touch (`(1, 3)` and `(3, 5)`) overlap; intervals that are merely adjacent (`(1, 2)` and `(3, 4)`) do not. Raise `ValueError` if any interval has `start > end`. The input must not be mutated.
- `insert(intervals, new) -> list[tuple[int, int]]`: insert `new` into a list of already merged, sorted intervals and return the merged result.
- `subtract(intervals, cut) -> list[tuple[int, int]]`: remove every integer point of `cut` from the (merged, sorted) intervals; e.g. `subtract([(1, 10)], (3, 5))` → `[(1, 2), (6, 10)]`.
- `total_length(intervals) -> int`: number of integer points covered, counting overlaps once (`[(1, 3), (2, 4)]` → 4).
