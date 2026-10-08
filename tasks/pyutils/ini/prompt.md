# ini

Implement `parse(text: str) -> dict[str, dict[str, str]]` in `solution.py`, a small INI parser:

- `[section]` headers (whitespace inside the brackets is stripped). Section names are case-sensitive; repeating a section merges into it.
- `key = value` or `key: value` (first `=` or `:` wins). Keys are stripped and lowercased; values are stripped. A later duplicate key overwrites the earlier one.
- Keys before the first section go into the section `"DEFAULT"`.
- Lines whose first non-space character is `;` or `#` are comments. Inline comments are **not** stripped.
- An indented non-empty line following a key continues that key's value: append `"\n"` plus the stripped line.
- Blank lines are ignored (and end a continuation).
- Values may contain `${key}` (same section, else `DEFAULT`) or `${section:key}` references, expanded after parsing, recursively. Raise `ValueError` on a missing reference or a reference cycle.
- Raise `ValueError` (with the 1-based line number in the message) for any other malformed line, e.g. a line with no `=`/`:`, an empty key, or an unterminated `[section`.
