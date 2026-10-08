"""Unit tests for the LLM proxy. The proxy is exercised over loopback; the
model provider and the upstream API are always mocked."""

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest import mock

from sia import proxy as proxy_mod
from sia.providers import ProviderError
from sia.proxy import Budget, LLMProxy, cost_usd

REAL_URLOPEN = urllib.request.urlopen


class CostTest(unittest.TestCase):
    def test_known_unknown_and_override_prices(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        self.assertAlmostEqual(cost_usd("claude-opus-5-5", usage), 24.0)
        self.assertAlmostEqual(cost_usd("mystery", usage), 60.0)
        self.assertAlmostEqual(cost_usd("claude-opus-5-5", usage, prices=(1.0, 2.0)), 3.0)

    def test_cache_tokens(self):
        usage = {"cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(cost_usd("x", usage, prices=(10.0, 0.0)), 1.0 + 12.5)
        self.assertEqual(cost_usd("x", {}), 0.0)


def headers(ctype: str, **extra) -> Message:
    m = Message()
    m["content-type"] = ctype
    for k, v in extra.items():
        m[k] = v
    return m


class FakeUpstreamResponse:
    def __init__(self, status: int, body: bytes = b"", ctype: str = "application/json", lines=(), **extra):
        self.status, self._body, self._lines = status, body, list(lines)
        self.headers = headers(ctype, **extra)

    def read(self):
        return self._body

    def __iter__(self):
        return iter(self._lines)


class ProxyTestBase(unittest.TestCase):
    proxy_kwargs: dict = {}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "calls.jsonl"
        kw = {"budget": Budget(max_calls=10, max_cost_usd=100.0), "host": "127.0.0.1", **self.proxy_kwargs}
        self.proxy = LLMProxy(self.log, **kw).start()

    def tearDown(self):
        self.proxy.stop()
        self._tmp.cleanup()

    def post(self, path="/v1/generate", body=None, token=None, raw=None, extra_headers=None):
        data = raw if raw is not None else json.dumps(body or {"model": "m", "messages": []}).encode()
        hdrs = {"authorization": f"Bearer {token or self.proxy.token}", "content-type": "application/json", **(extra_headers or {})}
        req = urllib.request.Request(f"http://127.0.0.1:{self.proxy.port}{path}", data=data, headers=hdrs, method="POST")
        try:
            with REAL_URLOPEN(req, timeout=10) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def post_json(self, *args, **kwargs):
        status, data, _ = self.post(*args, **kwargs)
        return status, json.loads(data)

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


class MockProxyTest(ProxyTestBase):
    proxy_kwargs = {"mock": lambda body: {"content": [], "usage": {"input_tokens": 10, "output_tokens": 5}, "model": body["model"]},
                    "force_model": "forced"}

    def test_auth_routes_and_bad_json(self):
        self.assertEqual(self.post_json(token="wrong")[0], 401)
        status, data = self.post_json(path="/v1/other")
        self.assertEqual((status, data["error"]["type"]), (404, "not_found_error"))
        self.assertEqual(self.post_json(raw=b"{not json")[0], 400)
        req = urllib.request.Request(f"http://127.0.0.1:{self.proxy.port}/v1/generate")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            REAL_URLOPEN(req, timeout=10)
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(self.records(), [])

    def test_x_api_key_auth_and_passthrough_path(self):
        status, data, _ = self.post(path="/v1/messages", extra_headers={"authorization": "", "x-api-key": self.proxy.token})
        self.assertEqual(status, 200)

    def test_forwards_to_mock_counts_and_logs(self):
        self.proxy.context["generation"] = 3
        self.proxy.start_task("t1", 5)
        status, data = self.post_json(body={"model": "asked", "messages": []})
        self.assertEqual((status, data["model"]), (200, "forced"))
        s = self.proxy.stats
        self.assertEqual((s.calls, s.input_tokens, s.output_tokens, s.models_requested), (1, 10, 5, {"asked": 1}))
        self.assertGreater(s.cost_usd, 0)
        (rec,) = self.records()
        self.assertEqual((rec["generation"], rec["task_id"], rec["call_index"], rec["status"]), (3, "t1", 1, 200))
        self.assertEqual((rec["requested_model"], rec["request"]["model"]), ("asked", "forced"))

    def test_mock_exception_is_500(self):
        self.proxy.mock = mock.Mock(side_effect=KeyError("x"))
        status, data = self.post_json()
        self.assertEqual(status, 500)
        self.assertIn("mock failed", data["error"]["message"])
        self.assertEqual((self.proxy.stats.calls, self.proxy.stats.errors), (1, 1))

    def test_count_tokens_not_counted(self):
        self.assertEqual(self.post_json(path="/v1/messages/count_tokens")[0], 200)
        self.assertEqual(self.proxy.stats.calls, 0)
        self.assertEqual(self.records(), [])

    def test_task_budget(self):
        self.proxy.start_task("t1", 1)
        self.assertFalse(self.proxy.task_exhausted)
        self.assertEqual(self.post_json()[0], 200)
        self.assertTrue(self.proxy.task_exhausted)
        status, data = self.post_json()
        self.assertEqual(status, 403)
        self.assertIn("Task model-call budget", data["error"]["message"])
        self.assertEqual((self.proxy.stats.rejected, self.proxy.task_rejected, self.proxy.stats.calls), (1, 1, 1))
        self.proxy.start_task("t2", 1)
        self.assertEqual((self.proxy.task_calls, self.proxy.task_rejected), (0, 0))
        self.assertEqual(self.post_json()[0], 200)

    def test_run_budget(self):
        self.proxy.budget = Budget(max_calls=1, max_cost_usd=100.0)
        self.assertFalse(self.proxy.exhausted)
        self.post_json()
        self.assertTrue(self.proxy.exhausted)
        status, data = self.post_json()
        self.assertEqual(status, 403)
        self.assertIn("Experiment budget", data["error"]["message"])
        self.proxy.budget = Budget(max_calls=100, max_cost_usd=0.0)
        self.assertTrue(self.proxy.exhausted)


class ProviderProxyTest(ProxyTestBase):
    proxy_kwargs = {"provider": "openai", "api_key": "sk-real"}

    def test_generate_uses_provider(self):
        resp = {"model": "m", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}, "provider": "openai"}
        with mock.patch.object(self.proxy.provider, "generate", return_value=(resp, {"raw": 1})) as gen:
            status, data = self.post_json(body={"model": "m", "messages": []})
        self.assertEqual((status, data), (200, resp))
        gen.assert_called_once_with({"model": "m", "messages": []})
        (rec,) = self.records()
        self.assertEqual((rec["provider"], rec["upstream_response"]), ("openai", {"raw": 1}))

    def test_provider_error(self):
        err = ProviderError(429, "slow down", raw={"r": 1})
        with mock.patch.object(self.proxy.provider, "generate", side_effect=err):
            status, data = self.post_json()
        self.assertEqual((status, data["error"]["message"]), (429, "slow down"))
        self.assertEqual(self.proxy.stats.errors, 1)
        self.assertEqual(self.records()[0]["upstream_response"], {"r": 1})

    def test_messages_path_not_proxied_for_other_providers(self):
        self.assertEqual(self.post_json(path="/v1/messages")[0], 404)


class AnthropicPassthroughTest(ProxyTestBase):
    proxy_kwargs = {"provider": "anthropic", "api_key": "sk-real", "upstream_url": "http://upstream.invalid"}

    def test_json_passthrough_uses_real_key(self):
        body = json.dumps({"content": [], "usage": {"input_tokens": 2, "output_tokens": 3}}).encode()
        fake = FakeUpstreamResponse(200, body, **{"request-id": "req_1"})
        with mock.patch.object(proxy_mod.urllib.request, "urlopen", return_value=fake) as up:
            status, data, hdrs = self.post(path="/v1/messages", extra_headers={"anthropic-beta": "b1"})
        self.assertEqual((status, json.loads(data)["usage"]["output_tokens"]), (200, 3))
        self.assertEqual(hdrs["request-id"], "req_1")
        sent = up.call_args[0][0]
        self.assertEqual(sent.full_url, "http://upstream.invalid/v1/messages")
        self.assertEqual(sent.get_header("X-api-key"), "sk-real")
        self.assertEqual(sent.get_header("Anthropic-beta"), "b1")
        self.assertEqual(self.proxy.stats.output_tokens, 3)

    def test_http_error_and_non_json(self):
        err = urllib.error.HTTPError("u", 529, "overloaded", headers(ctype="text/plain"), io.BytesIO(b"overloaded"))
        with mock.patch.object(proxy_mod.urllib.request, "urlopen", side_effect=err):
            status, data, _ = self.post(path="/v1/messages")
        self.assertEqual((status, data), (529, b"overloaded"))
        self.assertEqual(self.records()[0]["response"], {"raw": "overloaded"})

    def test_unreachable(self):
        with mock.patch.object(proxy_mod.urllib.request, "urlopen", side_effect=OSError("refused")):
            status, data = self.post_json(path="/v1/messages")
        self.assertEqual(status, 502)
        self.assertIn("upstream unreachable", data["error"]["message"])

    def test_stream_relay_reassembles_message(self):
        events = [
            {"type": "message_start", "message": {"id": "m1", "content": [], "usage": {"input_tokens": 7}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
        ]
        lines = [b"event: x\n"] + [b"data: " + json.dumps(e).encode() + b"\n" for e in events] + [b"data: {bad\n"]
        fake = FakeUpstreamResponse(200, ctype="text/event-stream", lines=lines)
        with mock.patch.object(proxy_mod.urllib.request, "urlopen", return_value=fake):
            status, data, _ = self.post(path="/v1/messages")
        self.assertEqual(status, 200)
        self.assertEqual(data, b"".join(lines))
        resp = self.records()[0]["response"]
        self.assertEqual(resp["content"][0]["text"], "Hello")
        self.assertEqual(resp["stop_reason"], "end_turn")
        self.assertEqual(resp["usage"], {"input_tokens": 7, "output_tokens": 4})
        self.assertEqual(self.proxy.stats.input_tokens, 7)


if __name__ == "__main__":
    unittest.main()
