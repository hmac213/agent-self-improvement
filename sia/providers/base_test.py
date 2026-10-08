import io
import json
import unittest
import urllib.error
from unittest import mock

from sia.providers import base
from sia.providers.base import Provider, ProviderError, error_body, meta, text_of


class EchoProvider(Provider):
    name = "echo"
    default_url = "https://echo.example/"

    def build_request(self, req):
        return f"{self.base_url}/gen", {"k": self.api_key}, {"prompt": req["prompt"]}

    def parse_response(self, data, req):
        return {"content": data["out"]}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HelpersTest(unittest.TestCase):
    def test_provider_error(self):
        e = ProviderError(429, "slow", raw={"x": 1})
        self.assertEqual((e.status, e.message, e.raw, str(e)), (429, "slow", {"x": 1}, "slow"))

    def test_error_body(self):
        self.assertEqual(error_body("api_error", "m"), {"type": "error", "error": {"type": "api_error", "message": "m"}})

    def test_text_of(self):
        self.assertEqual(text_of("s"), "s")
        self.assertEqual(text_of([{"type": "text", "text": "a"}, {"type": "x"}, "junk", {"text": "b"}]), "a\n\nb")
        self.assertEqual(text_of(None), "")

    def test_meta(self):
        self.assertEqual(meta({"provider_meta": {"p": {"s": 1}}}, "p"), {"s": 1})
        self.assertEqual(meta({"provider_meta": {"q": {}}}, "p"), {})
        self.assertEqual(meta({}, "p"), {})


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.p = EchoProvider("key", None, timeout=5)

    def test_init(self):
        self.assertEqual((self.p.api_key, self.p.base_url, self.p.timeout), ("key", "https://echo.example", 5))
        other = EchoProvider(None, "http://local:1/")
        self.assertEqual((other.api_key, other.base_url), ("", "http://local:1"))

    def test_abstract_translation(self):
        with self.assertRaises(NotImplementedError):
            Provider("k").build_request({})
        with self.assertRaises(NotImplementedError):
            Provider("k").parse_response({}, {})

    def test_error_message(self):
        self.assertEqual(self.p.error_message({"error": {"message": "nested"}}), "nested")
        self.assertEqual(self.p.error_message({"error": "flat"}), "flat")
        self.assertEqual(self.p.error_message({"other": 1}), '{"other": 1}')
        self.assertEqual(self.p.error_message([1]), "[1]")

    def test_generate_success(self):
        with mock.patch.object(base.urllib.request, "urlopen", return_value=FakeResponse(b'{"out": "hi"}')) as up:
            resp, raw = self.p.generate({"prompt": "p", "model": "m"})
        self.assertEqual(resp, {"content": "hi", "model": "m", "provider": "echo"})
        self.assertEqual(raw, {"out": "hi"})
        req = up.call_args[0][0]
        self.assertEqual((req.full_url, req.get_method(), json.loads(req.data)), ("https://echo.example/gen", "POST", {"prompt": "p"}))
        self.assertEqual((req.get_header("Content-type"), req.get_header("K")), ("application/json", "key"))
        self.assertEqual(up.call_args.kwargs["timeout"], 5)

    def http_error(self, code, body):
        return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(body))

    def test_generate_http_error(self):
        err = self.http_error(400, b'{"error": {"message": "bad request"}}')
        with mock.patch.object(base.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(ProviderError) as cm:
                self.p.generate({"prompt": "p"})
        self.assertEqual((cm.exception.status, cm.exception.message), (400, "echo: bad request"))
        self.assertEqual(cm.exception.raw, {"error": {"message": "bad request"}})

    def test_generate_http_error_non_json(self):
        with mock.patch.object(base.urllib.request, "urlopen", side_effect=self.http_error(503, b"<html>")):
            with self.assertRaises(ProviderError) as cm:
                self.p.generate({"prompt": "p"})
        self.assertEqual((cm.exception.status, cm.exception.raw), (503, {"raw": "<html>"}))

    def test_generate_network_and_parse_errors(self):
        with mock.patch.object(base.urllib.request, "urlopen", side_effect=OSError("refused")):
            with self.assertRaises(ProviderError) as cm:
                self.p.generate({"prompt": "p"})
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("unreachable", cm.exception.message)
        with mock.patch.object(base.urllib.request, "urlopen", return_value=FakeResponse(b"not json")):
            with self.assertRaises(ProviderError) as cm:
                self.p.generate({"prompt": "p"})
        self.assertIn("non-JSON", cm.exception.message)

    def test_generate_keeps_model_from_response(self):
        self.p.parse_response = lambda data, req: {"model": "served"}
        with mock.patch.object(base.urllib.request, "urlopen", return_value=FakeResponse(b"{}")):
            resp, _ = self.p.generate({"prompt": "p", "model": "asked"})
        self.assertEqual(resp["model"], "served")


if __name__ == "__main__":
    unittest.main()
