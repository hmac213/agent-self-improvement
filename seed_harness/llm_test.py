import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

HARNESS = str(Path(__file__).resolve().parent)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import llm as llm_mod  # noqa: E402
from llm import LLM, LLMError  # noqa: E402

CFG = {"model": "m", "max_tokens": 10, "effort": "low"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b""):
    return urllib.error.HTTPError("u", code, "msg", {}, io.BytesIO(body))


class LLMTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"SIA_LLM_URL": "http://proxy:1/", "SIA_LLM_TOKEN": "tok"})
        env.start()
        self.addCleanup(env.stop)
        self.urlopen = mock.patch.object(llm_mod.urllib.request, "urlopen").start()
        self.sleep = mock.patch.object(llm_mod.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.llm = LLM(CFG, max_retries=2, timeout=5)

    def test_init_from_environment(self):
        self.assertEqual((self.llm.url, self.llm.token), ("http://proxy:1/v1/generate", "tok"))
        with mock.patch.dict(os.environ, {"SIA_LLM_URL": "http://x"}, clear=True):
            self.assertEqual(LLM(CFG).token, "")

    def test_call_sends_request(self):
        self.urlopen.return_value = FakeResponse(b'{"content": []}')
        self.assertEqual(self.llm.call("sys", [{"role": "user"}], [{"name": "bash"}]), {"content": []})
        req = self.urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "http://proxy:1/v1/generate")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(json.loads(req.data), {"model": "m", "max_tokens": 10, "effort": "low", "system": "sys",
                                                "messages": [{"role": "user"}], "tools": [{"name": "bash"}]})
        self.assertEqual(self.urlopen.call_args.kwargs["timeout"], 5)

    def test_retries_retryable_errors_with_backoff(self):
        self.urlopen.side_effect = [http_error(529), OSError("refused"), FakeResponse(b'{"ok": 1}')]
        self.assertEqual(self.llm.call("s", [], []), {"ok": 1})
        self.assertEqual([c[0][0] for c in self.sleep.call_args_list], [1, 2])

    def test_gives_up_after_max_retries(self):
        self.urlopen.side_effect = OSError("refused")
        with self.assertRaises(LLMError) as cm:
            self.llm.call("s", [], [])
        self.assertEqual((cm.exception.status, self.urlopen.call_count), (0, 3))

    def test_non_retryable_error_raises_immediately(self):
        self.urlopen.side_effect = http_error(403, b'{"error": {"message": "budget exhausted"}}')
        with self.assertRaises(LLMError) as cm:
            self.llm.call("s", [], [])
        self.assertEqual((cm.exception.status, cm.exception.message), (403, "budget exhausted"))
        self.assertEqual(str(cm.exception), "403: budget exhausted")
        self.sleep.assert_not_called()

    def test_error_message(self):
        self.assertEqual(llm_mod._error_message(b'{"error": {"message": "m"}}'), "m")
        self.assertEqual(llm_mod._error_message(b"plain text"), "plain text")
        self.assertEqual(llm_mod._error_message(b'{"error": "flat"}'), '{"error": "flat"}')
        self.assertEqual(llm_mod._error_message(b"[1]"), "[1]")


if __name__ == "__main__":
    unittest.main()
