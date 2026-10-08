"""Scripted stand-in for the model API, for testing the scaffold without
spending tokens. Policies are deterministic functions of the request.

Policies only know how to solve the `tasks/smoke` suite, whose prompts say:
"Write the text `X` to a file named F in the working directory."
"""

from __future__ import annotations

import json
import re
import uuid

NOTE_TOOL = '''import os
SPEC = {"name": "note", "description": "Append a line to ~/notes.md.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
def run(input, ctx):
    with open(os.path.expanduser("~/notes.md"), "a") as f:
        f.write(input["text"] + "\\n")
    return "noted"
'''


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "tool_result":
            parts.append(_text_of(b.get("content", "")))
    return "\n".join(parts)


def _message(content: list, stop_reason: str, body: dict) -> dict:
    return {
        "id": f"msg_mock_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "mock"),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": len(json.dumps(body)) // 4, "output_tokens": 40},
    }


def _tool(name: str, input: dict, body: dict) -> dict:
    return _message([{"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:16]}", "name": name, "input": input}], "tool_use", body)


def _bash(cmd: str, body: dict) -> dict:
    return _tool("bash", {"command": cmd}, body)


def _restart(body: dict) -> dict:
    if any(t.get("name") == "restart" for t in body.get("tools", [])):
        return _tool("restart", {}, body)
    return _bash("kill -TERM $PPID", body)


def _solve_steps(body: dict, last: str) -> dict | None:
    if "SOLVED" in last:
        return _tool("submit", {}, body)
    task = _text_of(body["messages"][0]["content"])
    m = re.search(r"Write the text `([^`]*)` to a file named (\S+)", task)
    if not m:
        return _message([{"type": "text", "text": "I don't know how to do this task."}], "end_turn", body)
    return _bash(f"printf %s '{m.group(1)}' > {m.group(2)} && echo SOLVED", body)


def solve(body: dict) -> dict:
    last = _text_of(body["messages"][-1]["content"])
    return _solve_steps(body, last)


def self_modify(body: dict) -> dict:
    """Adds a new tool to its harness on the first task, restarts, then solves."""
    msgs = body["messages"]
    last = _text_of(msgs[-1]["content"])
    if len(msgs) == 1:
        return _bash("cat ~/.mock_patch 2>/dev/null || echo MOCK_PATCH_MISSING", body)
    if "MOCK_PATCH_MISSING" in last:
        return _bash(
            f"cat > ~/harness/tools/note.py <<'PYEOF'\n{NOTE_TOOL}PYEOF\n"
            "echo MOCK_PATCH_DONE > ~/.mock_patch && echo MOCK_PATCHED",
            body,
        )
    if "MOCK_PATCHED" in last:
        return _restart(body)
    return _solve_steps(body, last)


def break_harness(body: dict) -> dict:
    """Corrupts its harness once and kills it, to exercise rollback."""
    msgs = body["messages"]
    last = _text_of(msgs[-1]["content"])
    if len(msgs) == 1:
        return _bash("cat ~/.mock_broke 2>/dev/null || echo MOCK_NOT_BROKEN", body)
    if "MOCK_NOT_BROKEN" in last:
        return _bash("echo MOCK_BROKE > ~/.mock_broke; echo 'def broken(:' >> ~/harness/main.py; kill -TERM $PPID", body)
    return _solve_steps(body, last)


POLICIES = {"solve": solve, "self_modify": self_modify, "break_harness": break_harness}
