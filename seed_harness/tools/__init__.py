"""Tool registry. Every module in this package that defines `SPEC` (the tool
schema sent to the model) and `run(input, ctx) -> str` is loaded as a tool."""

import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path


class RestartRequested(Exception):
    """Raised by a tool to make the harness checkpoint and exit for a relaunch."""


@dataclass
class Context:
    workdir: Path
    task_id: str
    cfg: dict
    submitted: bool = False


def load_all() -> dict:
    tools = {}
    for info in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "SPEC") and hasattr(mod, "run"):
            tools[mod.SPEC["name"]] = mod
    return tools


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}"
