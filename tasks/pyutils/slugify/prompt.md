# slugify

Implement `slugify(text: str, max_length: int | None = None) -> str` in `solution.py`.

- Transliterate accented Latin characters to ASCII (`"Crème Brûlée"` → `"creme-brulee"`); drop any other non-ASCII characters.
- Lowercase the result.
- Runs of characters that are not ASCII letters or digits become a single `-`.
- No leading or trailing `-`.
- If `max_length` is given, truncate to at most that many characters without leaving a trailing `-`, and cut at a `-` boundary when possible: if truncation would split a word, drop the partial word (unless the first word alone is longer than `max_length`, in which case cut it hard).
- An input with no letters or digits returns `""`.
