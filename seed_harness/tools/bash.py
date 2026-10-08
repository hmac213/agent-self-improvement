import subprocess

from tools import truncate

SPEC = {
    "name": "bash",
    "description": "Run a shell command with bash in the task's working directory. Returns stdout and stderr.",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to run."},
            "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120)."},
        },
        "required": ["command"],
    },
}


def run(input: dict, ctx) -> str:
    try:
        proc = subprocess.run(
            ["bash", "-c", input["command"]],
            cwd=ctx.workdir,
            capture_output=True,
            text=True,
            timeout=input.get("timeout", 120),
        )
        out = proc.stdout + (f"\n[stderr]\n{proc.stderr}" if proc.stderr else "")
        out += f"\n[exit code {proc.returncode}]"
    except subprocess.TimeoutExpired:
        out = "[command timed out]"
    return truncate(out, ctx.cfg.get("max_tool_output_chars", 10000))
