import json
import unittest

from sia.providers.openai import OpenAIProvider


class OpenAIBuildRequestTest(unittest.TestCase):
    def setUp(self):
        self.p = OpenAIProvider("sk", None)

    def test_minimal(self):
        url, headers, body = self.p.build_request({"model": "gpt", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers, {"authorization": "Bearer sk"})
        self.assertEqual(body, {"model": "gpt", "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 16000})

    def test_conversation_translation(self):
        req = {
            "model": "gpt", "system": "sys", "max_tokens": 9, "effort": "low",
            "tools": [{"name": "bash", "description": "run", "input_schema": {"type": "object", "properties": {"c": {}}}},
                      {"name": "submit"}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
                {"role": "assistant", "content": [
                    {"type": "reasoning", "provider": "anthropic", "data": {}},
                    {"type": "tool_use", "id": "c1", "name": "bash", "input": {"c": "ls"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "c1", "content": "out", "is_error": True},
                    {"type": "text", "text": "notice"},
                ]},
                {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
            ],
        }
        _, _, body = self.p.build_request(req)
        self.assertEqual((body["max_completion_tokens"], body["reasoning_effort"]), (9, "low"))
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a\n\nb"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": '{"c": "ls"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "[error] out"},
            {"role": "user", "content": "notice"},
            {"role": "assistant", "content": "done"},
        ])
        self.assertEqual(body["tools"][0]["function"]["parameters"], req["tools"][0]["input_schema"])
        self.assertEqual(body["tools"][1]["function"], {"name": "submit", "description": "", "parameters": {"type": "object", "properties": {}}})


class OpenAIParseResponseTest(unittest.TestCase):
    def setUp(self):
        self.p = OpenAIProvider("sk", None)

    def parse(self, message, finish="stop", usage=None):
        return self.p.parse_response({"model": "gpt-x", "choices": [{"message": message, "finish_reason": finish}], "usage": usage or {}}, {})

    def test_text_and_usage(self):
        r = self.parse({"content": "hi"}, usage={"prompt_tokens": 10, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 4}})
        self.assertEqual((r["model"], r["content"], r["stop_reason"]), ("gpt-x", [{"type": "text", "text": "hi"}], "end_turn"))
        self.assertEqual(r["usage"], {"input_tokens": 6, "output_tokens": 2, "cache_read_input_tokens": 4})

    def test_tool_calls(self):
        calls = [
            {"id": "a", "function": {"name": "bash", "arguments": json.dumps({"c": 1})}},
            {"id": "b", "function": {"name": "bash", "arguments": "{broken"}},
            {"id": "c", "function": {"name": "bash", "arguments": "[1]"}},
            {"id": "d", "function": {"name": "submit", "arguments": ""}},
        ]
        r = self.parse({"content": None, "tool_calls": calls}, finish="tool_calls")
        self.assertEqual(r["stop_reason"], "tool_use")
        self.assertEqual([b["input"] for b in r["content"]],
                         [{"c": 1}, {"_unparsed_arguments": "{broken"}, {"_unparsed_arguments": "[1]"}, {}])

    def test_stop_reasons(self):
        self.assertEqual(self.parse({"content": "x"}, "length")["stop_reason"], "max_tokens")
        self.assertEqual(self.parse({"content": "x"}, "content_filter")["stop_reason"], "refusal")
        self.assertEqual(self.parse({"content": "x"}, "weird")["stop_reason"], "weird")
        r = self.parse({"refusal": "no"}, "stop")
        self.assertEqual((r["stop_reason"], r["content"]), ("refusal", [{"type": "text", "text": "no"}]))

    def test_empty(self):
        r = self.p.parse_response({}, {})
        self.assertEqual((r["content"], r["stop_reason"]), ([], None))
        self.assertEqual(r["usage"], {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0})


if __name__ == "__main__":
    unittest.main()
