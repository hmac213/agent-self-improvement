# expr

Implement `evaluate(expression: str, variables: dict[str, float] | None = None) -> float` in `solution.py`, an arithmetic evaluator. Do **not** use `eval`, `exec`, `compile` or the `ast` module.

- Numbers: integers and decimals (`3`, `2.5`, `.5`, `1e3`, `2.5E-2`).
- Operators by increasing precedence: `+ -` (left-assoc), `* / %` (left-assoc), unary `+ -`, `**` (right-assoc). Exponentiation binds tighter than unary minus on its left: `-2 ** 2 == -4`, but `2 ** -1 == 0.5`.
- Parentheses, arbitrary whitespace.
- Identifiers `[A-Za-z_][A-Za-z0-9_]*` look up `variables`; the functions `abs`, `min`, `max`, `sqrt` take comma-separated arguments (`max(1, x, 3)`; `min`/`max` need at least one argument).
- Return a `float`.
- Raise `ValueError` for syntax errors, unknown names, wrong argument counts, and division (or `%`) by zero.
