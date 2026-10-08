"""Model client. One interface for every model provider.

Requests go to the LLM endpoint at $SIA_LLM_URL, which translates them for
whichever provider serves the model (Anthropic, OpenAI, Gemini, ...), so this
client and the conversation format stay the same across providers.

POST $SIA_LLM_URL/v1/generate  (header "authorization: Bearer $SIA_LLM_TOKEN")
  {"model", "max_tokens", "effort", "system", "messages", "tools"}
  tool:    {"name", "description", "input_schema": <JSON Schema>}
  message: {"role": "user" | "assistant", "content": [block]}
  block:   {"type": "text", "text"}
           {"type": "tool_use", "id", "name", "input"}                     (assistant)
           {"type": "tool_result", "tool_use_id", "content", "is_error"}   (user)
           {"type": "reasoning", ...}  provider-specific; send back unchanged
  Blocks may carry "provider_meta"; send it back unchanged too.
Response:
  {"model", "content": [block], "stop_reason", "usage": {"input_tokens", "output_tokens", ...}}
  stop_reason is "end_turn", "tool_use", "max_tokens", "refusal" or provider-specific.
Errors: HTTP status plus {"type": "error", "error": {"type", "message"}}.
"""

import json
import os
import time
import urllib.error
import urllib.request

RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504, 529}


class LLMError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class LLM:
    def __init__(self, cfg: dict, max_retries: int = 4, timeout: float = 1800):
        self.cfg = cfg
        self.url = os.environ["SIA_LLM_URL"].rstrip("/") + "/v1/generate"
        self.token = os.environ.get("SIA_LLM_TOKEN", "")
        self.max_retries = max_retries
        self.timeout = timeout

    def call(self, system: str, messages: list, tools: list) -> dict:
        return self._post({
            "model": self.cfg["model"],
            "max_tokens": self.cfg["max_tokens"],
            "effort": self.cfg.get("effort"),
            "system": system,
            "messages": messages,
            "tools": tools,
        })

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode()
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.token}"}
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                status, message = e.code, _error_message(e.read())
            except OSError as e:  # connection refused, timeout, ...
                status, message = 0, str(e)
            if attempt == self.max_retries or (status and status not in RETRY_STATUSES):
                raise LLMError(status, message)
            time.sleep(min(2 ** attempt, 30))


def _error_message(raw: bytes) -> str:
    try:
        return json.loads(raw)["error"]["message"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return raw.decode(errors="replace")[:2000]
