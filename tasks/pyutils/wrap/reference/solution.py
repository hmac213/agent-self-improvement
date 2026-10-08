import re


def _fill(words, width):
    lines, cur = [], []
    for w in words:
        while len(w) > width:
            if cur:
                lines.append(cur)
                cur = []
            lines.append([w[:width]])
            w = w[width:]
        if cur and len(" ".join(cur + [w])) > width:
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _justify(words, width):
    if len(words) == 1:
        return words[0]
    gaps = len(words) - 1
    spaces = width - sum(len(w) for w in words)
    base, extra = divmod(spaces, gaps)
    out = ""
    for i, w in enumerate(words[:-1]):
        out += w + " " * (base + (1 if i < extra else 0))
    return out + words[-1]


def wrap(text: str, width: int, justify: bool = False) -> list[str]:
    if width < 1:
        raise ValueError(width)
    paragraphs = [p.split() for p in re.split(r"\n\s*\n", text)]
    out = []
    for words in paragraphs:
        if not words:
            continue
        lines = _fill(words, width)
        if out:
            out.append("")
        for i, line in enumerate(lines):
            last = i == len(lines) - 1
            out.append(_justify(line, width) if justify and not last else " ".join(line))
    return out
