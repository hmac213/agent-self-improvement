"""Model providers behind the proxy's provider-neutral /v1/generate endpoint
(protocol in base.py). Adding one: subclass Provider with build_request and
parse_response, and register it here."""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import Provider, ProviderError, error_body
from .gemini import GeminiProvider
from .openai import OpenAIProvider

PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}

# Host environment variables holding each provider's key, in order of preference.
API_KEY_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}
ALL_KEY_ENV = tuple(v for vs in API_KEY_ENV.values() for v in vs)


def make_provider(name: str, api_key: str | None = None, base_url: str | None = None) -> Provider:
    try:
        cls = PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; known: {sorted(PROVIDERS)}") from None
    return cls(api_key, base_url)


__all__ = ["PROVIDERS", "API_KEY_ENV", "ALL_KEY_ENV", "Provider", "ProviderError", "error_body", "make_provider"]
