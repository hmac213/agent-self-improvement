"""LLM proxy between the sandbox and the model API.

The harness is mutable, so anything it reports about itself is untrustworthy.
Every model call goes through this proxy instead, which:
  * serves one provider-neutral endpoint, POST /v1/generate (protocol in
    providers/base.py), and translates it for the run's provider (Anthropic,
    OpenAI, Gemini); POST /v1/messages is also passed through verbatim when
    the provider is Anthropic,
  * authenticates the sandbox with a per-run token (the real API key never
    enters the sandbox),
  * enforces the run's call and cost budget,
  * optionally pins the model,
  * logs every request and response to llm_calls.jsonl and, through
    `recorder`, to the run's trajectory database (trajectories.py) — the
    ground-truth trace for analysis.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .providers import ProviderError, error_body, make_provider

GENERATE_PATH = "/v1/generate"
ANTHROPIC_PATHS = ("/v1/messages", "/v1/messages/count_tokens")
# USD per million tokens: (input, output). Cache reads at 10% of input, writes at 125%.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, usage: dict, prices: tuple[float, float] | None = None) -> float:
    pin, pout = prices or PRICES.get(model, (10.0, 50.0))  # unknown models priced conservatively
    return (
        usage.get("input_tokens", 0) * pin
        + usage.get("cache_read_input_tokens", 0) * pin * 0.1
        + usage.get("cache_creation_input_tokens", 0) * pin * 1.25
        + usage.get("output_tokens", 0) * pout
    ) / 1e6


@dataclass
class Budget:
    max_calls: int
    max_cost_usd: float


@dataclass
class Stats:
    calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    rejected: int = 0
    models_requested: dict = field(default_factory=dict)


class LLMProxy:
    def __init__(
        self,
        log_path: Path,
        budget: Budget,
        provider: str = "anthropic",
        upstream_url: str | None = None,
        api_key: str | None = None,
        mock: Callable[[dict], dict] | None = None,
        force_model: str | None = None,
        host: str = "0.0.0.0",
        port: int = 0,
        prices: tuple[float, float] | None = None,
        recorder: Callable[[dict], None] | None = None,
    ):
        self.log_path = log_path
        self.budget = budget
        self.provider = make_provider(provider, api_key, upstream_url)
        self.upstream_url = self.provider.base_url
        self.api_key = api_key
        self.prices = prices
        self.recorder = recorder  # called with each log record, e.g. TrajectoryDB.recorder(run_id)
        self.mock = mock
        self.force_model = force_model
        self.token = "sia-" + secrets.token_urlsafe(24)
        self.stats = Stats()
        self.context: dict = {}  # merged into each log record (generation, task_id), set by the supervisor
        self.task_calls0 = 0
        self.task_limit: int | None = None
        self.task_rejected = 0  # calls refused since the current task started
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler_class())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "LLMProxy":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def exhausted(self) -> bool:
        return self.stats.calls >= self.budget.max_calls or self.stats.cost_usd >= self.budget.max_cost_usd

    def start_task(self, task_id: str, limit: int) -> None:
        with self._lock:
            self.context["task_id"] = task_id
            self.task_calls0 = self.stats.calls
            self.task_limit = limit
            self.task_rejected = 0

    @property
    def task_calls(self) -> int:
        return self.stats.calls - self.task_calls0

    @property
    def task_exhausted(self) -> bool:
        return self.task_limit is not None and self.task_calls >= self.task_limit

    def _log(self, record: dict) -> None:
        with self._lock, open(self.log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
        if self.recorder is not None:
            self.recorder(record)

    def _handler_class(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence default stderr logging
                pass

            def _send_json(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _error(self, status: int, etype: str, message: str) -> None:
                self._send_json(status, {"type": "error", "error": {"type": etype, "message": message}})

            def do_GET(self):
                self._error(404, "not_found_error", f"only POST {GENERATE_PATH} is proxied")

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length)
                key = self.headers.get("x-api-key") or self.headers.get("authorization", "").removeprefix("Bearer ")
                if key != proxy.token:
                    return self._error(401, "authentication_error", "invalid token")
                path = self.path.split("?")[0]
                passthrough = proxy.mock is not None or proxy.provider.name == "anthropic"
                if path != GENERATE_PATH and not (passthrough and path in ANTHROPIC_PATHS):
                    return self._error(404, "not_found_error", f"{path} is not proxied; use POST {GENERATE_PATH}")
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    return self._error(400, "invalid_request_error", "body is not JSON")

                requested = body.get("model")
                with proxy._lock:
                    proxy.stats.models_requested[requested] = proxy.stats.models_requested.get(requested, 0) + 1
                if proxy.force_model:
                    body["model"] = proxy.force_model
                if path == "/v1/messages/count_tokens":
                    return self._forward(path, body, requested, count_only=True)
                if proxy.exhausted or proxy.task_exhausted:
                    with proxy._lock:
                        proxy.stats.rejected += 1
                        proxy.task_rejected += 1
                    # 403 so SDKs don't retry. Rejected calls don't count toward budgets.
                    what = "Experiment" if proxy.exhausted else "Task model-call"
                    return self._error(403, "permission_error", f"{what} budget exhausted.")
                self._forward(path, body, requested)

            def _forward(self, path: str, body: dict, requested: str | None, count_only: bool = False) -> None:
                t0 = time.time()
                record = {"ts": t0, **proxy.context, "path": path, "requested_model": requested, "request": body}
                if path == GENERATE_PATH and proxy.mock is None:
                    record["provider"] = proxy.provider.name
                    try:
                        resp, record["upstream_response"] = proxy.provider.generate(body)
                        status = 200
                    except ProviderError as e:
                        status, resp = e.status, error_body("api_error", e.message)
                        record["upstream_response"] = e.raw
                    send = lambda: self._send_json(status, resp)  # noqa: E731
                elif proxy.mock is not None:
                    try:
                        status, resp = 200, proxy.mock(body)
                    except Exception as e:
                        status, resp = 500, {"type": "error", "error": {"type": "api_error", "message": f"mock failed: {e!r}"}}
                    send = lambda: self._send_json(status, resp)  # noqa: E731
                else:
                    status, resp, send = self._upstream(path, body)
                if not count_only:
                    # Count and log before replying, so the harness's next request
                    # always sees this one in the budget.
                    usage = (resp or {}).get("usage") or {}
                    cost = cost_usd(body.get("model", ""), usage, proxy.prices)
                    with proxy._lock:
                        proxy.stats.calls += 1
                        proxy.stats.errors += status != 200
                        proxy.stats.input_tokens += usage.get("input_tokens", 0)
                        proxy.stats.output_tokens += usage.get("output_tokens", 0)
                        proxy.stats.cost_usd += cost
                        record["call_index"] = proxy.stats.calls
                    proxy._log({**record, "status": status, "response": resp, "usage": usage, "cost_usd": cost, "latency_s": time.time() - t0})
                if send is not None:
                    send()

            def _upstream(self, path: str, body: dict):
                """Returns (status, parsed response, send) where send() writes the reply,
                or is None if the reply was already relayed (streaming)."""
                headers = {
                    "content-type": "application/json",
                    "x-api-key": proxy.api_key or "",
                    "anthropic-version": self.headers.get("anthropic-version", "2023-06-01"),
                }
                if self.headers.get("anthropic-beta"):
                    headers["anthropic-beta"] = self.headers["anthropic-beta"]
                req = urllib.request.Request(proxy.upstream_url + path, data=json.dumps(body).encode(), headers=headers, method="POST")
                try:
                    resp = urllib.request.urlopen(req, timeout=1800)
                    status = resp.status
                except urllib.error.HTTPError as e:
                    resp, status = e, e.code
                except OSError as e:
                    err = {"type": "error", "error": {"type": "api_error", "message": f"upstream unreachable: {e}"}}
                    return 502, err, lambda: self._send_json(502, err)
                ctype = resp.headers.get("content-type", "application/json")
                if ctype.startswith("text/event-stream"):
                    return status, self._relay_stream(resp, status), None
                data = resp.read()
                extra = {h: resp.headers[h] for h in ("retry-after", "request-id") if resp.headers.get(h)}

                def send():
                    self.send_response(status)
                    self.send_header("content-type", ctype)
                    self.send_header("content-length", str(len(data)))
                    for k, v in extra.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)

                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    parsed = {"raw": data.decode(errors="replace")}
                return status, parsed, send

            def _relay_stream(self, resp, status: int) -> dict:
                """Pass an SSE stream through and reassemble the message for the log."""
                self.send_response(status)
                self.send_header("content-type", "text/event-stream")
                self.send_header("connection", "close")
                self.end_headers()
                self.close_connection = True
                message: dict = {"content": []}
                usage: dict = {}
                for line in resp:
                    self.wfile.write(line)
                    self.wfile.flush()
                    if not line.startswith(b"data:"):
                        continue
                    try:
                        ev = json.loads(line[5:])
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "message_start":
                        message = ev["message"]
                        usage.update(message.get("usage") or {})
                    elif ev.get("type") == "content_block_start":
                        message.setdefault("content", []).append(ev["content_block"])
                    elif ev.get("type") == "content_block_delta":
                        block, delta = message["content"][ev["index"]], ev["delta"]
                        for k in ("text", "thinking", "partial_json"):
                            if k in delta:
                                block[k] = block.get(k, "") + delta[k]
                    elif ev.get("type") == "message_delta":
                        message.update(ev.get("delta") or {})
                        usage.update(ev.get("usage") or {})
                message["usage"] = usage
                return message

        return Handler
