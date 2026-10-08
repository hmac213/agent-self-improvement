"""Google Gemini API (generateContent)."""

from __future__ import annotations

import urllib.parse
import uuid

from .base import Provider, meta, text_of

REFUSALS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}
GENERATED_ID = "gemini_"  # prefix of call ids we made up because Gemini sent none


class GeminiProvider(Provider):
    name = "gemini"
    default_url = "https://generativelanguage.googleapis.com"

    def _part(self, b: dict, names: dict) -> dict | None:
        t = b.get("type")
        if t == "reasoning":
            return b.get("data") if b.get("provider") == self.name else None
        if t == "text":
            part = {"text": b["text"]} if b.get("text") else None
        elif t == "tool_use":
            names[b["id"]] = b["name"]
            call = {"name": b["name"], "args": b.get("input") or {}}
            if not b["id"].startswith(GENERATED_ID):
                call["id"] = b["id"]
            part = {"functionCall": call}
        elif t == "tool_result":
            out = text_of(b.get("content", ""))
            resp = {"name": names.get(b["tool_use_id"], "unknown"), "response": {"error" if b.get("is_error") else "output": out}}
            if not b["tool_use_id"].startswith(GENERATED_ID):
                resp["id"] = b["tool_use_id"]
            part = {"functionResponse": resp}
        else:
            return None
        if part is not None and meta(b, self.name).get("thoughtSignature"):
            part["thoughtSignature"] = meta(b, self.name)["thoughtSignature"]
        return part

    def build_request(self, req: dict) -> tuple[str, dict, dict]:
        names: dict[str, str] = {}  # tool_use id -> function name, for functionResponse parts
        contents = []
        for m in req["messages"]:
            blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
            parts = [p for p in (self._part(b, names) for b in blocks) if p is not None]
            if parts:
                contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
        body: dict = {"contents": contents, "generationConfig": {"maxOutputTokens": req.get("max_tokens", 16000)}}
        if req.get("system"):
            body["systemInstruction"] = {"parts": [{"text": req["system"]}]}
        if req.get("tools"):
            decls = []
            for t in req["tools"]:
                d = {"name": t["name"], "description": t.get("description", "")}
                schema = t.get("input_schema") or {}
                if schema.get("properties"):
                    d["parametersJsonSchema"] = schema
                decls.append(d)
            body["tools"] = [{"functionDeclarations": decls}]
        if req.get("effort"):
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": req["effort"]}
        model = urllib.parse.quote(req["model"].removeprefix("models/"), safe="")
        url = f"{self.base_url}/v1beta/models/{model}:generateContent"
        return url, {"x-goog-api-key": self.api_key}, body

    def parse_response(self, data: dict, req: dict) -> dict:
        cands = data.get("candidates") or []
        cand = cands[0] if cands else {}
        content = []
        for p in (cand.get("content") or {}).get("parts") or []:
            sig = {"provider_meta": {self.name: {"thoughtSignature": p["thoughtSignature"]}}} if p.get("thoughtSignature") else {}
            if p.get("thought"):
                content.append({"type": "reasoning", "provider": self.name, "data": p})
            elif "functionCall" in p:
                fc = p["functionCall"]
                content.append({"type": "tool_use", "id": fc.get("id") or f"{GENERATED_ID}{uuid.uuid4().hex[:16]}",
                                "name": fc.get("name", ""), "input": fc.get("args") or {}, **sig})
            elif "text" in p:
                content.append({"type": "text", "text": p["text"], **sig})
            elif sig:  # a bare signature part; keep it so it is sent back
                content.append({"type": "reasoning", "provider": self.name, "data": p})
        finish = cand.get("finishReason")
        if not cands or finish in REFUSALS:
            stop = "refusal"
        elif any(b["type"] == "tool_use" for b in content):
            stop = "tool_use"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in (None, "STOP"):
            stop = "end_turn"
        else:
            stop = finish.lower()
        u = data.get("usageMetadata") or {}
        cached = u.get("cachedContentTokenCount") or 0
        return {
            "model": data.get("modelVersion") or req.get("model"),
            "content": content,
            "stop_reason": stop,
            "usage": {"input_tokens": (u.get("promptTokenCount") or 0) - cached,
                      "output_tokens": (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0),
                      "cache_read_input_tokens": cached},
        }
