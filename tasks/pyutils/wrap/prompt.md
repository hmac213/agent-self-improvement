# wrap

Implement `wrap(text: str, width: int, justify: bool = False) -> list[str]` in `solution.py`:

- Paragraphs are separated by one or more blank lines (lines containing only whitespace). Within a paragraph, all whitespace runs (including newlines) collapse to single spaces.
- Fill each paragraph greedily: put as many words on a line as fit within `width` characters.
- A word longer than `width` is split into chunks of exactly `width` characters (the final chunk may be shorter), and following words may continue on the line after the final chunk if they fit.
- Separate paragraphs in the output with a single empty string `""`. No leading or trailing empty lines.
- If `justify` is true, every line of a paragraph except its last is padded to exactly `width` by distributing extra spaces between words as evenly as possible, with the leftmost gaps getting the extra spaces. Single-word lines are left as is.
- Raise `ValueError` if `width < 1`. Empty or whitespace-only text returns `[]`.
