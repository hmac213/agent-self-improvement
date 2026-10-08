import unittest

from sia.providers.gemini import GENERATED_ID, GeminiProvider


class GeminiBuildRequestTest(unittest.TestCase):
    def setUp(self):
        self.p = GeminiProvider("gk", None)

    def test_minimal(self):
        url, headers, body = self.p.build_request({"model": "models/gemini-x", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent")
        self.assertEqual(headers, {"x-goog-api-key": "gk"})
        self.assertEqual(body, {"contents": [{"role": "user", "parts": [{"text": "hi"}]}], "generationConfig": {"maxOutputTokens": 16000}})

    def test_model_name_is_url_quoted(self):
        url, _, _ = self.p.build_request({"model": "a/b c", "messages": []})
        self.assertTrue(url.endswith("/models/a%2Fb%20c:generateContent"))

    def test_conversation_translation(self):
        req = {
            "model": "g", "system": "sys", "max_tokens": 7, "effort": "low",
            "tools": [{"name": "bash", "description": "run", "input_schema": {"type": "object", "properties": {"c": {}}}},
                      {"name": "submit", "input_schema": {"type": "object", "properties": {}}}],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": ""}]},  # becomes empty, dropped
                {"role": "assistant", "content": [
                    {"type": "reasoning", "provider": "gemini", "data": {"thought": True, "text": "t"}},
                    {"type": "reasoning", "provider": "openai", "data": {}},
                    {"type": "text", "text": "ok", "provider_meta": {"gemini": {"thoughtSignature": "S1"}}},
                    {"type": "tool_use", "id": "real1", "name": "bash", "input": {"c": "ls"}},
                    {"type": "tool_use", "id": GENERATED_ID + "abc", "name": "submit", "input": None},
                    {"type": "image"},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "real1", "content": "out"},
                    {"type": "tool_result", "tool_use_id": GENERATED_ID + "abc", "content": "bad", "is_error": True},
                    {"type": "tool_result", "tool_use_id": "zzz", "content": "?"},
                ]},
            ],
        }
        _, _, body = self.p.build_request(req)
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "sys"}]})
        self.assertEqual(body["generationConfig"], {"maxOutputTokens": 7, "thinkingConfig": {"thinkingLevel": "low"}})
        decls = body["tools"][0]["functionDeclarations"]
        self.assertIn("parametersJsonSchema", decls[0])
        self.assertEqual(decls[1], {"name": "submit", "description": ""})
        self.assertEqual(len(body["contents"]), 2)
        model, user = body["contents"]
        self.assertEqual(model["role"], "model")
        self.assertEqual(model["parts"], [
            {"thought": True, "text": "t"},
            {"text": "ok", "thoughtSignature": "S1"},
            {"functionCall": {"name": "bash", "args": {"c": "ls"}, "id": "real1"}},
            {"functionCall": {"name": "submit", "args": {}}},
        ])
        self.assertEqual(user["parts"], [
            {"functionResponse": {"name": "bash", "response": {"output": "out"}, "id": "real1"}},
            {"functionResponse": {"name": "submit", "response": {"error": "bad"}}},
            {"functionResponse": {"name": "unknown", "response": {"output": "?"}, "id": "zzz"}},
        ])


class GeminiParseResponseTest(unittest.TestCase):
    def setUp(self):
        self.p = GeminiProvider("gk", None)

    def parse(self, parts, finish="STOP", usage=None, cands=True):
        data = {"usageMetadata": usage or {}, "modelVersion": "g-001"}
        if cands:
            data["candidates"] = [{"content": {"parts": parts}, "finishReason": finish}]
        return self.p.parse_response(data, {"model": "g"})

    def test_parts(self):
        parts = [
            {"thought": True, "text": "thinking"},
            {"functionCall": {"id": "c1", "name": "bash", "args": {"c": "ls"}}, "thoughtSignature": "S"},
            {"functionCall": {"name": "submit"}},
            {"text": "hi"},
            {"thoughtSignature": "S2"},
            {"inlineData": {}},
        ]
        r = self.parse(parts, usage={"promptTokenCount": 10, "cachedContentTokenCount": 3, "candidatesTokenCount": 2, "thoughtsTokenCount": 5})
        c = r["content"]
        self.assertEqual(r["model"], "g-001")
        self.assertEqual(c[0], {"type": "reasoning", "provider": "gemini", "data": parts[0]})
        self.assertEqual(c[1], {"type": "tool_use", "id": "c1", "name": "bash", "input": {"c": "ls"},
                                "provider_meta": {"gemini": {"thoughtSignature": "S"}}})
        self.assertTrue(c[2]["id"].startswith(GENERATED_ID))
        self.assertEqual(c[2]["input"], {})
        self.assertEqual(c[3], {"type": "text", "text": "hi"})
        self.assertEqual(c[4], {"type": "reasoning", "provider": "gemini", "data": {"thoughtSignature": "S2"}})
        self.assertEqual(len(c), 5)
        self.assertEqual(r["stop_reason"], "tool_use")
        self.assertEqual(r["usage"], {"input_tokens": 7, "output_tokens": 7, "cache_read_input_tokens": 3})

    def test_stop_reasons(self):
        text = [{"text": "x"}]
        self.assertEqual(self.parse(text, "STOP")["stop_reason"], "end_turn")
        self.assertEqual(self.parse(text, None)["stop_reason"], "end_turn")
        self.assertEqual(self.parse(text, "MAX_TOKENS")["stop_reason"], "max_tokens")
        self.assertEqual(self.parse(text, "SAFETY")["stop_reason"], "refusal")
        self.assertEqual(self.parse(text, "MALFORMED_FUNCTION_CALL")["stop_reason"], "malformed_function_call")
        r = self.parse([], cands=False)
        self.assertEqual((r["stop_reason"], r["content"]), ("refusal", []))
        self.assertEqual(r["usage"], {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0})

    def test_signature_round_trip(self):
        r = self.parse([{"text": "hi", "thoughtSignature": "S"}])
        _, _, body = self.p.build_request({"model": "g", "messages": [{"role": "assistant", "content": r["content"]}]})
        self.assertEqual(body["contents"][0]["parts"], [{"text": "hi", "thoughtSignature": "S"}])


if __name__ == "__main__":
    unittest.main()
