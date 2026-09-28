"""The only module in jobfit that imports the Anthropic SDK. Nothing under
jobfit/scrape/ imports this at module level; bootstrap.build_discovery_planner
imports it lazily, and only the discovery command calls that."""

from __future__ import annotations

import json

from jobfit.scrape.errors import ClassifierFailed


class AnthropicLLMClient:
    """LLMClient over the Messages API with a JSON-schema structured output.
    Deterministic settings (temperature 0); one classification per call."""

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 60.0):
        import anthropic

        self._anthropic = anthropic
        kwargs = {"timeout": timeout, "max_retries": 2}
        if api_key:
            kwargs["api_key"] = api_key
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        a = self._anthropic
        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=0, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except a.RateLimitError as error:
            raise ClassifierFailed(f"rate limited: {error}") from error
        except a.APIStatusError as error:
            raise ClassifierFailed(f"api error {error.status_code}: {error}") from error
        except a.APIConnectionError as error:
            raise ClassifierFailed(f"connection error: {error}") from error
        if getattr(response, "stop_reason", None) == "refusal":
            raise ClassifierFailed("model refused the request")
        text = next((block.text for block in response.content if getattr(block, "type", "") == "text"), "")
        try:
            return json.loads(text)
        except ValueError as error:
            raise ClassifierFailed(f"non-JSON response: {text[:200]!r}") from error
