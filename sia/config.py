"""Experiment configuration, loaded from TOML. Every field has a default so an
experiment file only needs to state what it changes."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ExperimentCfg:
    name: str = "unnamed"
    seed_harness: str = "seed_harness"
    # How much the agent is told about its own harness (see prompts.py):
    #   0 none, 1 aware of harness location + restart semantics,
    #   2 aware + `restart` tool, 3 explicitly invited to self-modify (positive control)
    affordance: int = 0
    system_prompt: str | None = None  # overrides the rendered prompt entirely
    replicates: int = 1
    # SQLite trajectory store (see trajectories.py). None = <runs root>/trajectories.db;
    # a relative path is resolved against the runs root.
    trajectory_db: str | None = None


@dataclass
class ModelCfg:
    name: str = "claude-opus-5-5"
    effort: str | None = "medium"  # passed to the provider as-is (Anthropic effort, OpenAI reasoning_effort, Gemini thinkingLevel)
    max_tokens: int = 16000
    # Provider the proxy calls: anthropic | openai | gemini (see sia/providers), or mock.
    # The real key is read on the host from ANTHROPIC_API_KEY, OPENAI_API_KEY or
    # GEMINI_API_KEY (or GOOGLE_API_KEY) and never enters the sandbox.
    upstream: str = "anthropic"
    upstream_url: str | None = None  # None = the provider's public API; or e.g. an OpenAI-compatible server
    # USD per million (input, output) tokens, for the cost budget. None = the
    # built-in table in proxy.py, which prices unknown models conservatively.
    price_per_mtok: list[float] | None = None
    mock_policy: str = "solve"  # see mock_llm.POLICIES
    # "force": the proxy rewrites every request to `name` (the agent can't swap models);
    # "allow": the harness may choose any model.
    model_policy: str = "force"


@dataclass
class SandboxCfg:
    kind: str = "local"  # local | docker | daytona
    image: str = "sia-sandbox:latest"  # docker image / daytona image
    # URL at which the sandbox reaches the host's LLM proxy. Derived for local
    # and docker; required for daytona (e.g. a cloudflared tunnel).
    proxy_public_url: str | None = None
    proxy_port: int = 0  # 0 = pick a free port; pin it when tunnelling to a remote sandbox
    daytona_network_allow_list: str | None = None
    # docker / daytona: "proxy_only" = the sandbox reaches only the LLM proxy;
    # "open" = docker's default bridge (internet and host) / daytona with only
    # daytona_network_allow_list applied.
    network: str = "proxy_only"
    # The local sandbox can't be isolated (the harness is a host process with
    # your permissions), so it only runs the mock model unless this is set.
    allow_unisolated: bool = False
    keep: bool = False  # leave the sandbox running after the run (docker/daytona)


@dataclass
class TasksCfg:
    suite: str = "tasks/pyutils"
    order: str = "fixed"  # fixed | shuffled
    epochs: int = 1
    feedback: str = "score"  # none | score | failures
    seed: int = 0


@dataclass
class LimitsCfg:
    task_llm_calls: int = 40
    task_wall_seconds: int = 1200
    total_llm_calls: int = 1000
    total_cost_usd: float = 25.0
    wall_seconds: int = 4 * 3600
    max_generations: int = 100
    boot_grace_seconds: float = 30.0
    boot_failures_before_rollback: int = 2
    finish_grace_seconds: float = 30.0
    # After a budget runs out, wait for the harness to hit it (a refused call) or
    # go quiet this long before grading, so tools from the last call can finish.
    budget_grace_seconds: float = 150.0
    stall_seconds: float = 900.0  # kill a harness that makes no model calls for this long


@dataclass
class Config:
    experiment: ExperimentCfg = field(default_factory=ExperimentCfg)
    model: ModelCfg = field(default_factory=ModelCfg)
    sandbox: SandboxCfg = field(default_factory=SandboxCfg)
    tasks: TasksCfg = field(default_factory=TasksCfg)
    limits: LimitsCfg = field(default_factory=LimitsCfg)

    def to_dict(self) -> dict:
        return asdict(self)

    def resolve(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else REPO_ROOT / p


def _fill(cls, data: dict):
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"unknown keys for [{cls.__name__}]: {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        default = known[name].default_factory() if callable(known[name].default_factory) else None
        kwargs[name] = _fill(type(default), value) if is_dataclass(default) else value
    return cls(**kwargs)


def load(path: str | Path, overrides: dict | None = None) -> Config:
    data = tomllib.loads(Path(path).read_text())
    for dotted, value in (overrides or {}).items():
        section, key = dotted.split(".", 1)
        data.setdefault(section, {})[key] = value
    return _fill(Config, data)
