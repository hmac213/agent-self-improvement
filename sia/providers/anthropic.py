"""Anthropic Messages API. The canonical protocol is modelled on it, so the
translation is mostly a pass-through."""

from __future__ import annotations

from .base import Provider, text_of

REASONING_TYPES = ("thinking", "redacted_thinking")


class AnthropicProvider(Provider):
    name = "anthropic"
    default_url = "https://api.anthropic.com"

    def _block(self, b: dict) -> dict | None:
        t = b.get("type")
        if t == "reasoning":
            return b.get("data") if b.get("provider") == self.name else None
        if t == "text":
            return {"type": "text", "text": b["text"]} if b.get("text") else None
        if t == "tool_use":
            return {"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}}
        if t == "tool_result":
            out = {"type": "tool_result", "tool_use_id": b["tool_use_id"], "content": text_of(b.get("content", ""))}
            if b.get("is_error"):
                out["is_error"] = True
            return out
        return {k: v for k, v in b.items() if k != "provider_meta"}  # other Anthropic-native blocks

    def build_request(self, req: dict) -> tuple[str, dict, dict]:
        messages = []
        for m in req["messages"]:
            content = m["content"]
            if isinstance(content, list):
                content = [x for x in (self._block(b) for b in content) if x is not None]
            messages.append({"role": m["role"], "content": content})
        body = {"model": req["model"], "max_tokens": req.get("max_tokens", 16000), "messages": messages}
        if req.get("system"):
            body["system"] = req["system"]
        if req.get("tools"):
            body["tools"] = [{k: t[k] for k in ("name", "description", "input_schema") if k in t} for t in req["tools"]]
        if req.get("effort"):
            body["output_config"] = {"effort": req["effort"]}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        return f"{self.base_url}/v1/messages", headers, body

    def parse_response(self, data: dict, req: dict) -> dict:
        content = []
        for b in data.get("content") or []:
            if b.get("type") in REASONING_TYPES:
                content.append({"type": "reasoning", "provider": self.name, "data": b})
            elif b.get("type") == "tool_use":
                content.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}})
            else:
                content.append(b)
        return {
            "model": data.get("model"),
            "content": content,
            "stop_reason": data.get("stop_reason"),
            "usage": {k: v for k, v in (data.get("usage") or {}).items() if isinstance(v, int)},
        }
