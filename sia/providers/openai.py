"""OpenAI Chat Completions API. Also works with OpenAI-compatible servers
(vLLM, Ollama, OpenRouter, ...) through `model.upstream_url`."""

from __future__ import annotations

import json

from .base import Provider, text_of

STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use",
                "length": "max_tokens", "content_filter": "refusal"}


class OpenAIProvider(Provider):
    name = "openai"
    default_url = "https://api.openai.com"

    def build_request(self, req: dict) -> tuple[str, dict, dict]:
        messages = [{"role": "system", "content": req["system"]}] if req.get("system") else []
        for m in req["messages"]:
            blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
            text = "\n\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))
            if m["role"] == "assistant":
                calls = [
                    {"id": b["id"], "type": "function",
                     "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                    for b in blocks if b.get("type") == "tool_use"
                ]
                msg = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                messages.append(msg)
                continue
            # Tool results become "tool" messages, which must directly follow the
            # assistant message that made the calls; any text follows them.
            for b in blocks:
                if b.get("type") == "tool_result":
                    out = text_of(b.get("content", ""))
                    messages.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                                     "content": f"[error] {out}" if b.get("is_error") else out})
            if text:
                messages.append({"role": "user", "content": text})
        body = {"model": req["model"], "messages": messages, "max_completion_tokens": req.get("max_tokens", 16000)}
        if req.get("tools"):
            body["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
                for t in req["tools"]
            ]
        if req.get("effort"):
            body["reasoning_effort"] = req["effort"]
        return f"{self.base_url}/v1/chat/completions", {"authorization": f"Bearer {self.api_key}"}, body

    def parse_response(self, data: dict, req: dict) -> dict:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        if msg.get("refusal"):
            content.append({"type": "text", "text": msg["refusal"]})
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                args = {"_unparsed_arguments": fn.get("arguments")}
            content.append({"type": "tool_use", "id": call["id"], "name": fn.get("name", ""), "input": args})
        finish = choice.get("finish_reason")
        stop = "refusal" if msg.get("refusal") else STOP_REASONS.get(finish, finish)
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return {
            "model": data.get("model"),
            "content": content,
            "stop_reason": stop,
            "usage": {"input_tokens": (u.get("prompt_tokens") or 0) - cached,
                      "output_tokens": u.get("completion_tokens") or 0,
                      "cache_read_input_tokens": cached},
        }
