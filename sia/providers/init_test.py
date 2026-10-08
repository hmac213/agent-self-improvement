"""Tests for sia/providers/__init__.py (registry and make_provider)."""

import unittest

from sia import providers
from sia.providers import ALL_KEY_ENV, API_KEY_ENV, PROVIDERS, make_provider


class RegistryTest(unittest.TestCase):
    def test_every_provider_has_key_env(self):
        self.assertEqual(set(PROVIDERS), set(API_KEY_ENV))
        for name, cls in PROVIDERS.items():
            self.assertEqual(cls.name, name)
        self.assertEqual(ALL_KEY_ENV, ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"))

    def test_make_provider(self):
        p = make_provider("openai", "k", "http://x/")
        self.assertIsInstance(p, providers.OpenAIProvider)
        self.assertEqual((p.api_key, p.base_url), ("k", "http://x"))
        self.assertEqual(make_provider("gemini").base_url, providers.GeminiProvider.default_url)

    def test_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "unknown provider 'nope'; known: \\['anthropic', 'gemini', 'openai'\\]"):
            make_provider("nope")


if __name__ == "__main__":
    unittest.main()
