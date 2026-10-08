# roman

Implement two functions in `solution.py`:

- `to_roman(n: int) -> str` for 1 ≤ n ≤ 3999, using standard subtractive notation (`4` → `"IV"`, `1994` → `"MCMXCIV"`). Raise `ValueError` outside that range or for non-integers (including `bool`).
- `from_roman(s: str) -> int` that accepts only canonical numerals — exactly the strings `to_roman` can produce, uppercase only — and raises `ValueError` for anything else (`"IIII"`, `"IC"`, `"VX"`, `""`, `"mcm"`).
