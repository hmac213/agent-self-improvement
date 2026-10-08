"""Provider-neutral model protocol and the base class for provider clients.

The harness speaks one protocol, whatever model runs it. The proxy receives it
at POST /v1/generate and a provider client translates it to and from the
provider's own API, so the harness doesn't change when the provider does.

Request:
  {"model": str, "max_tokens": int, "effort": str | None,
   "system": str, "messages": [message], "tools": [tool]}
  tool:    {"name", "description", "input_schema": <JSON Schema>}
  message: {"role": "user" | "assistant", "content": [block]}
  block:   {"type": "text", "text"}
           {"type": "tool_use", "id", "name", "input": {...}}             (assistant)
           {"type": "tool_result", "tool_use_id", "content": str, "is_error"}  (user)
           {"type": "reasoning", "provider", "data"}                      (assistant)
  A reasoning block is the provider's own record of its reasoning (e.g. a signed
  thinking block). It is sent back only to the provider that produced it. Any
  block may also carry "provider_meta": {<provider>: {...}}, data a provider
  needs to see again on later turns (e.g. Gemini thought signatures).

Response:
  {"model", "provider", "content": [block],
   "stop_reason": "end_turn" | "tool_use" | "max_tokens" | "refusal" | <other>,
   "usage": {"input_tokens", "output_tokens", "cache_read_input_tokens", ...}}
  usage follows Anthropic's convention: input_tokens excludes cache reads.

Errors are returned as {"type": "error", "error": {"type", "message"}} with the
provider's HTTP status.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class ProviderError(Exception):
    def __init__(self, status: int, message: str, raw=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.raw = raw


def error_body(etype: str, message: str) -> dict:
    return {"type": "error", "error": {"type": etype, "message": message}}


def text_of(content) -> str:
    """Flatten tool_result content (a string or a list of text blocks) to a string."""
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict))


def meta(block: dict, provider: str) -> dict:
    return (block.get("provider_meta") or {}).get(provider) or {}


class Provider:
    name: str = ""
    default_url: str = ""

    def __init__(self, api_key: str | None, base_url: str | None = None, timeout: float = 1800):
        self.api_key = api_key or ""
        self.base_url = (base_url or self.default_url).rstrip("/")
        self.timeout = timeout

    # --- translation, implemented per provider --------------------------------
    def build_request(self, req: dict) -> tuple[str, dict, dict]:
        """Canonical request -> (url, headers, provider body)."""
        raise NotImplementedError

    def parse_response(self, data: dict, req: dict) -> dict:
        """Provider response body -> canonical response."""
        raise NotImplementedError

    def error_message(self, data) -> str:
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return err["message"]
            if isinstance(err, str):
                return err
        return json.dumps(data)[:2000]

    # --- transport ------------------------------------------------------------
    def generate(self, req: dict) -> tuple[dict, dict]:
        """Call the provider. Returns (canonical response, raw provider response).
        Raises ProviderError on an HTTP or network error."""
        url, headers, body = self.build_request(req)
        hreq = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(hreq, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw.decode(errors="replace")}
            raise ProviderError(e.code, f"{self.name}: {self.error_message(data)}", data) from None
        except OSError as e:
            raise ProviderError(502, f"{self.name} unreachable: {e}") from None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ProviderError(502, f"{self.name} returned non-JSON: {raw[:500]!r}") from None
        resp = self.parse_response(data, req)
        resp.setdefault("model", req.get("model"))
        resp["provider"] = self.name
        return resp, data
