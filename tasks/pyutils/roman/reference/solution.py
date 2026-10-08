VALUES = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n: int) -> str:
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3999:
        raise ValueError(n)
    out = []
    for v, sym in VALUES:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


_TABLE = {to_roman(i): i for i in range(1, 4000)}


def from_roman(s: str) -> int:
    if not isinstance(s, str) or s not in _TABLE:
        raise ValueError(s)
    return _TABLE[s]
