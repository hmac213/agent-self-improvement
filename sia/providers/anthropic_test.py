import unittest

from sia.providers.anthropic import AnthropicProvider

THINKING = {"type": "thinking", "thinking": "hmm", "signature": "sig"}


class AnthropicProviderTest(unittest.TestCase):
    def setUp(self):
        self.p = AnthropicProvider("sk", None)

    def test_build_request_minimal(self):
        url, headers, body = self.p.build_request({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers, {"x-api-key": "sk", "anthropic-version": "2023-06-01"})
        self.assertEqual(body, {"model": "m", "max_tokens": 16000, "messages": [{"role": "user", "content": "hi"}]})

    def test_build_request_full(self):
        req = {
            "model": "m", "max_tokens": 5, "system": "sys", "effort": "high",
            "tools": [{"name": "bash", "description": "d", "input_schema": {"type": "object"}, "extra": 1}],
            "messages": [
                {"role": "assistant", "content": [
                    {"type": "reasoning", "provider": "anthropic", "data": THINKING},
                    {"type": "reasoning", "provider": "gemini", "data": {"x": 1}},
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "ok", "provider_meta": {"gemini": {}}},
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": None},
                    {"type": "image", "source": {}, "provider_meta": {"x": 1}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "out"}], "is_error": True},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "fine", "is_error": False},
                ]},
            ],
        }
        _, _, body = self.p.build_request(req)
        self.assertEqual((body["max_tokens"], body["system"], body["output_config"]), (5, "sys", {"effort": "high"}))
        self.assertEqual(body["tools"], [{"name": "bash", "description": "d", "input_schema": {"type": "object"}}])
        self.assertEqual(body["messages"][0]["content"], [
            THINKING,
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
            {"type": "image", "source": {}},
        ])
        self.assertEqual(body["messages"][1]["content"], [
            {"type": "tool_result", "tool_use_id": "t1", "content": "out", "is_error": True},
            {"type": "tool_result", "tool_use_id": "t2", "content": "fine"},
        ])

    def test_custom_base_url(self):
        url, _, _ = AnthropicProvider("k", "http://local:9/").build_request({"model": "m", "messages": []})
        self.assertEqual(url, "http://local:9/v1/messages")

    def test_parse_response(self):
        data = {
            "model": "m-1", "stop_reason": "tool_use",
            "content": [THINKING, {"type": "redacted_thinking", "data": "x"}, {"type": "text", "text": "hi"},
                        {"type": "tool_use", "id": "t", "name": "bash", "input": {"command": "ls"}, "caller": "x"}],
            "usage": {"input_tokens": 3, "output_tokens": 4, "service_tier": "standard", "cache_creation": {"a": 1}},
        }
        r = self.p.parse_response(data, {})
        self.assertEqual((r["model"], r["stop_reason"]), ("m-1", "tool_use"))
        self.assertEqual(r["content"][0], {"type": "reasoning", "provider": "anthropic", "data": THINKING})
        self.assertEqual(r["content"][1]["type"], "reasoning")
        self.assertEqual(r["content"][2], {"type": "text", "text": "hi"})
        self.assertEqual(r["content"][3], {"type": "tool_use", "id": "t", "name": "bash", "input": {"command": "ls"}})
        self.assertEqual(r["usage"], {"input_tokens": 3, "output_tokens": 4})

    def test_parse_empty_response(self):
        self.assertEqual(self.p.parse_response({}, {}), {"model": None, "content": [], "stop_reason": None, "usage": {}})

    def test_reasoning_round_trip(self):
        parsed = self.p.parse_response({"content": [THINKING]}, {})
        _, _, body = self.p.build_request({"model": "m", "messages": [{"role": "assistant", "content": parsed["content"]}]})
        self.assertEqual(body["messages"][0]["content"], [THINKING])


if __name__ == "__main__":
    unittest.main()
