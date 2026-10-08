"""Provider clients: translation to and from each provider's API, and the
proxy's /v1/generate endpoint against fake upstreams."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sia.providers import make_provider
from sia.proxy import Budget, LLMProxy

TOOLS = [
    {"name": "bash", "description": "Run a command.", "input_schema": {
        "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "submit", "description": "Submit.", "input_schema": {"type": "object", "properties": {}}},
]


def conversation(call_id="call_1", reasoning=None, call_meta=None):
    """A canonical request with one completed tool round trip."""
    assistant = [{"type": "text", "text": "Listing files."},
                 {"type": "tool_use", "id": call_id, "name": "bash", "input": {"command": "ls"}, **(call_meta or {})}]
    if reasoning:
        assistant.insert(0, reasoning)
    return {
        "model": "some-model", "max_tokens": 1000, "effort": "high", "system": "You are an agent.", "tools": TOOLS,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Do the task."}]},
            {"role": "assistant", "content": assistant},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call_id, "content": "a.txt", "is_error": False},
                {"type": "text", "text": "[Notice] hurry"}]},
        ],
    }


# --- Anthropic -------------------------------------------------------------------
def test_anthropic_request():
    thinking = {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    req = conversation(reasoning={"type": "reasoning", "provider": "anthropic", "data": thinking})
    req["messages"][1]["content"].insert(0, {"type": "reasoning", "provider": "openai", "data": {"x": 1}})
    url, headers, body = make_provider("anthropic", "k").build_request(req)
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "k"
    assert body["system"] == "You are an agent." and body["output_config"] == {"effort": "high"}
    assert body["messages"][1]["content"][0] == thinking  # own reasoning replayed, other providers' dropped
    assert len(body["messages"][1]["content"]) == 3
    assert body["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "call_1", "content": "a.txt"}
    assert body["tools"][0]["input_schema"]["required"] == ["command"]


def test_anthropic_response():
    data = {"model": "claude-x", "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 7, "server_tool_use": {}},
            "content": [{"type": "thinking", "thinking": "t", "signature": "s"},
                        {"type": "tool_use", "id": "toolu_1", "name": "bash", "input": {"command": "ls"}}]}
    resp = make_provider("anthropic", "k").parse_response(data, {})
    assert resp["content"][0] == {"type": "reasoning", "provider": "anthropic", "data": data["content"][0]}
    assert resp["content"][1]["type"] == "tool_use" and resp["stop_reason"] == "tool_use"
    assert resp["usage"] == {"input_tokens": 5, "output_tokens": 7}


# --- OpenAI ----------------------------------------------------------------------
def test_openai_request():
    url, headers, body = make_provider("openai", "k").build_request(conversation())
    assert url == "https://api.openai.com/v1/chat/completions" and headers["authorization"] == "Bearer k"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    call = body["messages"][2]["tool_calls"][0]
    assert call["id"] == "call_1" and json.loads(call["function"]["arguments"]) == {"command": "ls"}
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"}
    assert body["messages"][4]["content"] == "[Notice] hurry"
    assert body["tools"][0]["function"]["parameters"]["required"] == ["command"]
    assert body["reasoning_effort"] == "high" and body["max_completion_tokens"] == 1000


def test_openai_response():
    data = {"model": "gpt-x", "usage": {"prompt_tokens": 100, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 40}},
            "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "ok", "tool_calls": [
                {"id": "call_9", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"pwd\"}"}},
                {"id": "call_10", "type": "function", "function": {"name": "bash", "arguments": "not json"}}]}}]}
    resp = make_provider("openai", "k").parse_response(data, {})
    assert resp["content"][0] == {"type": "text", "text": "ok"}
    assert resp["content"][1] == {"type": "tool_use", "id": "call_9", "name": "bash", "input": {"command": "pwd"}}
    assert resp["content"][2]["input"] == {"_unparsed_arguments": "not json"}
    assert resp["stop_reason"] == "tool_use"
    assert resp["usage"] == {"input_tokens": 60, "output_tokens": 9, "cache_read_input_tokens": 40}


# --- Gemini ----------------------------------------------------------------------
def test_gemini_request():
    req = conversation(call_meta={"provider_meta": {"gemini": {"thoughtSignature": "SIG"}}})
    url, headers, body = make_provider("gemini", "k").build_request({**req, "model": "gemini-x"})
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    assert headers["x-goog-api-key"] == "k"
    assert body["systemInstruction"] == {"parts": [{"text": "You are an agent."}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    call = body["contents"][1]["parts"][1]
    assert call == {"functionCall": {"name": "bash", "args": {"command": "ls"}, "id": "call_1"}, "thoughtSignature": "SIG"}
    result = body["contents"][2]["parts"][0]["functionResponse"]
    assert result == {"name": "bash", "response": {"output": "a.txt"}, "id": "call_1"}
    decls = body["tools"][0]["functionDeclarations"]
    assert "parametersJsonSchema" in decls[0] and "parametersJsonSchema" not in decls[1]
    assert body["generationConfig"] == {"maxOutputTokens": 1000, "thinkingConfig": {"thinkingLevel": "high"}}


def test_gemini_response_round_trip():
    g = make_provider("gemini", "k")
    data = {"modelVersion": "gemini-x", "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 5, "thoughtsTokenCount": 20},
            "candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
                {"text": "thinking...", "thought": True},
                {"functionCall": {"name": "bash", "args": {"command": "ls"}}, "thoughtSignature": "SIG"}]}}]}
    resp = g.parse_response(data, {"model": "gemini-x"})
    assert resp["stop_reason"] == "tool_use" and resp["usage"]["output_tokens"] == 25
    assert resp["content"][0]["type"] == "reasoning"
    call = resp["content"][1]
    assert call["type"] == "tool_use" and call["provider_meta"] == {"gemini": {"thoughtSignature": "SIG"}}
    # Replaying the turn sends the signature back and no made-up id.
    req = {"model": "gemini-x", "messages": [
        {"role": "user", "content": [{"type": "text", "text": "go"}]},
        {"role": "assistant", "content": resp["content"]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"], "content": "boom", "is_error": True}]}]}
    _, _, body = g.build_request(req)
    parts = body["contents"][1]["parts"]
    assert parts[0] == {"text": "thinking...", "thought": True}
    assert parts[1] == {"functionCall": {"name": "bash", "args": {"command": "ls"}}, "thoughtSignature": "SIG"}
    assert body["contents"][2]["parts"][0] == {"functionResponse": {"name": "bash", "response": {"error": "boom"}}}


def test_gemini_blocked_prompt_is_a_refusal():
    resp = make_provider("gemini", "k").parse_response({"promptFeedback": {"blockReason": "SAFETY"}}, {"model": "g"})
    assert resp["stop_reason"] == "refusal" and resp["content"] == []


# --- through the proxy -----------------------------------------------------------
RESPONSES = {
    "anthropic": {"model": "m", "stop_reason": "end_turn", "content": [{"type": "text", "text": "hi"}],
                  "usage": {"input_tokens": 1000, "output_tokens": 100}},
    "openai": {"model": "m", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
               "usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
    "gemini": {"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [{"text": "hi"}]}}],
               "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 100}},
}


class FakeUpstream:
    def __init__(self, reply: dict, status: int = 200):
        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.append((self.path, dict(self.headers), body))
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def post(proxy, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{proxy.port}{path}", data=json.dumps(body).encode(),
                                 headers={"authorization": f"Bearer {proxy.token}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def make_proxy(tmp_path):
    started = []

    def make(provider, reply, status=200, **kw):
        up = FakeUpstream(reply, status)
        p = LLMProxy(tmp_path / f"{provider}.jsonl", Budget(10, 100), provider=provider, upstream_url=up.url,
                     api_key="real-key", force_model="pinned", host="127.0.0.1", **kw).start()
        started.append((p, up))
        return p, up

    yield make
    for p, up in started:
        p.stop()
        up.server.shutdown()


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
def test_generate_through_proxy(make_proxy, provider):
    p, up = make_proxy(provider, RESPONSES[provider])
    status, resp = post(p, "/v1/generate", conversation())
    assert status == 200
    assert resp["content"] == [{"type": "text", "text": "hi"}] and resp["stop_reason"] == "end_turn"
    assert resp["provider"] == provider and resp["usage"]["output_tokens"] == 100
    path, headers, body = up.seen[0]
    assert "real-key" in json.dumps(headers) and p.token not in json.dumps(headers)
    assert "pinned" in path or body.get("model") == "pinned"  # model pinning reaches every provider
    rec = json.loads(p.log_path.read_text().splitlines()[0])
    assert rec["provider"] == provider and rec["requested_model"] == "some-model"
    assert rec["response"] == resp and rec["upstream_response"] == RESPONSES[provider]
    assert p.stats.calls == 1 and p.stats.cost_usd > 0


def test_provider_errors_are_relayed(make_proxy):
    p, _ = make_proxy("openai", {"error": {"message": "bad model", "type": "invalid_request_error"}}, status=404)
    status, resp = post(p, "/v1/generate", conversation())
    assert status == 404 and "bad model" in resp["error"]["message"]
    assert p.stats.errors == 1


def test_anthropic_passthrough_only_for_anthropic(make_proxy):
    p, _ = make_proxy("gemini", RESPONSES["gemini"])
    status, resp = post(p, "/v1/messages", {"model": "x", "max_tokens": 5, "messages": []})
    assert status == 404 and "/v1/generate" in resp["error"]["message"]


def test_price_override(make_proxy):
    p, _ = make_proxy("openai", RESPONSES["openai"], prices=(1.0, 2.0))
    post(p, "/v1/generate", conversation())
    assert p.stats.cost_usd == pytest.approx((1000 * 1.0 + 100 * 2.0) / 1e6)
