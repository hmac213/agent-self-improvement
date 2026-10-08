"""Model client."""

import anthropic


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        # Base URL and API key come from ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY.
        self.client = anthropic.Anthropic(max_retries=4, timeout=900)

    def call(self, system: str, messages: list, tools: list):
        kwargs = dict(
            model=self.cfg["model"],
            max_tokens=self.cfg["max_tokens"],
            system=system,
            messages=messages,
            tools=tools,
        )
        if self.cfg.get("effort"):
            kwargs["output_config"] = {"effort": self.cfg["effort"]}
        return self.client.messages.create(**kwargs)
