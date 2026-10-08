"""Checkpointed conversation state, saved after every step so a restarted
process resumes where the previous one stopped."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

AGENT_HOME = Path(os.environ.get("SIA_AGENT_HOME", Path.home()))
CHECKPOINT = AGENT_HOME / "state" / "checkpoint.json"


@dataclass
class State:
    task_id: str | None = None
    messages: list = field(default_factory=list)
    submitted: bool = False
    notice_offset: int = 0
    pending: list = field(default_factory=list)  # text to add to the next user turn

    def save(self) -> None:
        CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
        tmp = CHECKPOINT.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self)))
        os.replace(tmp, CHECKPOINT)

    def start_task(self, task: dict) -> None:
        self.task_id = task["task_id"]
        self.messages = [{"role": "user", "content": [{"type": "text", "text": task["prompt"]}]}]
        self.submitted = False

    def close_dangling_tool_calls(self, reason: str) -> None:
        """If the last assistant turn requested tools that never returned, answer them."""
        if not self.messages or self.messages[-1]["role"] != "assistant":
            return
        calls = [b for b in self.messages[-1]["content"] if b.get("type") == "tool_use"]
        if calls:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b["id"], "content": reason, "is_error": True}
                for b in calls
            ]})


def load() -> tuple[State, bool]:
    """Return (state, resumed) where resumed is True if a checkpoint existed."""
    try:
        return State(**json.loads(CHECKPOINT.read_text())), True
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return State(), False
