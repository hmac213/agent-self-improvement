from __future__ import annotations

from pathlib import Path

from ..config import SandboxCfg
from .base import Sandbox, SandboxPaths


def make_sandbox(cfg: SandboxCfg, run_dir: Path, run_id: str, proxy_port: int | None = None) -> Sandbox:
    """`proxy_port` is the host port of the run's LLM proxy (docker's proxy_only network forwards to it)."""
    if cfg.kind == "local":
        from .local import LocalSandbox

        return LocalSandbox(run_dir / "sandbox")
    if cfg.kind == "docker":
        from .docker import DockerSandbox

        return DockerSandbox(cfg.image, name=f"sia-{run_id}", network=cfg.network, proxy_port=proxy_port)
    if cfg.kind == "daytona":
        from .daytona import DaytonaSandbox

        return DaytonaSandbox(cfg.image, cfg.proxy_public_url, cfg.daytona_network_allow_list, labels={"run": run_id},
                              network=cfg.network)
    raise ValueError(f"unknown sandbox kind {cfg.kind!r}")


__all__ = ["Sandbox", "SandboxPaths", "make_sandbox"]
