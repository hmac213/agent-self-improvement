"""Mailbox shared with the supervisor that hands out tasks.

Files in $SIA_ENV_DIR:
  task.json      current task ({"task_id", "prompt", "workdir", ...}) or {"done": true}
  submit.json    written by us to hand in the current task ({"task_id": ...})
  notices.jsonl  messages from the supervisor, one JSON object per line ({"text": ...})
"""

import json
import os
from pathlib import Path

ENV_DIR = Path(os.environ.get("SIA_ENV_DIR", "/env"))


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def current_task() -> dict | None:
    try:
        return json.loads((ENV_DIR / "task.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def submit(task_id: str) -> None:
    _write_atomic(ENV_DIR / "submit.json", json.dumps({"task_id": task_id}))


def read_notices(offset: int) -> tuple[list[str], int]:
    """Return notices after line `offset` and the new offset."""
    try:
        lines = (ENV_DIR / "notices.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return [], offset
    texts = []
    for line in lines[offset:]:
        try:
            texts.append(json.loads(line)["text"])
        except (json.JSONDecodeError, KeyError):
            pass
    return texts, len(lines)
