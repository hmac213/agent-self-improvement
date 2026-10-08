"""Harness snapshots: one copy per generation, so every self-modification is
recorded and any version can be restored or reused as a seed."""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

IGNORE = ("__pycache__", ".pyc", ".sia-tmp")


def _files(root: Path) -> dict[str, Path]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not any(i in rel for i in IGNORE):
            out[rel] = p
    return out


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for rel, p in _files(root).items():
        h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def diff_trees(old: Path, new: Path) -> tuple[str, dict]:
    """Unified diff between two harness trees and summary stats."""
    a, b = _files(old) if old.exists() else {}, _files(new)
    chunks, stats = [], {"added": [], "removed": [], "modified": [], "lines_added": 0, "lines_removed": 0}
    for rel in sorted(set(a) | set(b)):
        if rel in a and rel in b and a[rel].read_bytes() == b[rel].read_bytes():
            continue
        kind = "added" if rel not in a else "removed" if rel not in b else "modified"
        stats[kind].append(rel)
        old_lines = _lines(a.get(rel))
        new_lines = _lines(b.get(rel))
        for line in difflib.unified_diff(old_lines, new_lines, f"a/{rel}", f"b/{rel}"):
            chunks.append(line if line.endswith("\n") else line + "\n")
            if line.startswith("+") and not line.startswith("+++"):
                stats["lines_added"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                stats["lines_removed"] += 1
    return "".join(chunks), stats


def _lines(p: Path | None) -> list[str]:
    if p is None:
        return []
    try:
        return p.read_text().splitlines(keepends=True)
    except UnicodeDecodeError:
        return [f"<binary file, {p.stat().st_size} bytes>\n"]
