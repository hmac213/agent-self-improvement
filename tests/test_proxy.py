import json
import urllib.error
import urllib.request

import pytest

from sia import mock_llm
from sia.proxy import Budget, LLMProxy


@pytest.fixture
def proxy(tmp_path):
    p = LLMProxy(tmp_path / "log.jsonl", Budget(max_calls=2, max_cost_usd=100), mock=mock_llm.solve,
                 force_model="claude-opus-5-5", host="127.0.0.1").start()
    yield p
    p.stop()


def post(proxy, body, key=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{proxy.port}/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": key or proxy.token, "content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


BODY = {"model": "claude-haiku-5-5", "max_tokens": 10, "messages": [{"role": "user", "content": "Write the text `x` to a file named a.txt"}]}


def test_rejects_bad_token(proxy):
    assert post(proxy, BODY, key="wrong")[0] == 401


def test_forces_model_and_logs(proxy):
    status, resp = post(proxy, BODY)
    assert status == 200 and resp["model"] == "claude-opus-5-5"
    rec = json.loads(proxy.log_path.read_text().splitlines()[0])
    assert rec["requested_model"] == "claude-haiku-5-5" and rec["request"]["model"] == "claude-opus-5-5"
    assert proxy.stats.models_requested == {"claude-haiku-5-5": 1}


def test_budget(proxy):
    assert post(proxy, BODY)[0] == 200
    assert post(proxy, BODY)[0] == 200
    status, resp = post(proxy, BODY)
    assert status == 403 and "budget" in resp["error"]["message"]


class _FakeUpstream:
    """Minimal stand-in for the Messages API that records the key it was given."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.append((self.headers["x-api-key"], body))
                usage = {"input_tokens": 1000, "output_tokens": 100}
                if body.get("stream"):
                    events = [
                        {"type": "message_start", "message": {"id": "m", "role": "assistant", "content": [], "usage": {"input_tokens": 1000}}},
                        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hel"}},
                        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
                        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 100}},
                    ]
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for ev in events:
                        self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode())
                    return
                data = json.dumps({"id": "m", "content": [{"type": "text", "text": "hi"}], "usage": usage}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


def test_forwards_with_real_key_and_relays_streams(tmp_path):
    up = _FakeUpstream()
    p = LLMProxy(tmp_path / "log.jsonl", Budget(10, 100), upstream_url=up.url, api_key="real-key", host="127.0.0.1").start()
    try:
        status, resp = post(p, BODY)
        assert status == 200 and resp["content"][0]["text"] == "hi"
        assert up.seen[0][0] == "real-key"
        req = urllib.request.Request(
            f"http://127.0.0.1:{p.port}/v1/messages", data=json.dumps({**BODY, "stream": True}).encode(),
            headers={"x-api-key": p.token, "content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req) as r:
            assert b"message_delta" in r.read()
        recs = [json.loads(line) for line in p.log_path.read_text().splitlines()]
        assert recs[1]["response"]["content"][0]["text"] == "hello"
        assert recs[1]["usage"] == {"input_tokens": 1000, "output_tokens": 100}
        assert p.stats.calls == 2 and p.stats.cost_usd > 0
    finally:
        p.stop()
        up.server.shutdown()
