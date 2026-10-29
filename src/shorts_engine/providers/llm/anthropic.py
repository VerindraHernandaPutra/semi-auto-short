import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .base import BaseLLMProvider, LLMOutputError, LLMProviderError, LLMUsage

T = TypeVar("T", bound=BaseModel)

PROVIDER_NAME = "anthropic"
DEFAULT_EFFORT = "medium"
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_TOKENS = 16000  # thinking tokens count against this, so keep generous headroom
MAX_REPORTED_ERRORS = 5


def describe_validation_error(exc: ValidationError) -> str:
    """Where the output broke the schema (field path and error type), never the output itself."""
    parts = [
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['type']}"
        for error in exc.errors()[:MAX_REPORTED_ERRORS]
    ]
    return "; ".join(parts)


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str | None = None, client: anthropic.Anthropic | None = None):
        key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY")
        if client is None:
            if not key:
                raise LLMProviderError(PROVIDER_NAME, "ANTHROPIC_API_KEY is not set")
            client = anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)
        self.client = client

    def generate_structured(
        self, prompt: str, schema: type[T], model_name: str, effort: str | None = None
    ) -> T:
        effort = effort or DEFAULT_EFFORT
        if effort not in VALID_EFFORTS:
            raise ValueError(f"Unknown effort level: {effort}")

        # Structured outputs (not forced tool_choice, which newer models reject with HTTP 400).
        # The schema is sent the way messages.parse() sends it, but the response is validated here,
        # after its usage has been recorded: a draft that breaks the schema has still been billed.
        output_format = {
            "type": "json_schema",
            "schema": anthropic.transform_schema(schema.model_json_schema()),
        }
        try:
            response = self.client.messages.create(
                model=model_name,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": effort, "format": output_format},
            )
        except anthropic.APIStatusError as exc:
            raise LLMProviderError(
                PROVIDER_NAME, f"request failed (HTTP {exc.status_code}): {exc.message}", model_name
            ) from None
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(
                PROVIDER_NAME, f"network error ({type(exc).__name__})", model_name
            ) from None

        usage = getattr(response, "usage", None)
        if self.usage_listener is not None and usage is not None:
            self.usage_listener(
                LLMUsage(
                    provider=PROVIDER_NAME,
                    model=model_name,
                    operation=schema.__name__,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            )

        if response.stop_reason in ("refusal", "max_tokens"):
            raise LLMProviderError(
                PROVIDER_NAME, f"no usable output (stop_reason={response.stop_reason})", model_name
            )
        text = next(
            (block.text for block in response.content if getattr(block, "type", None) == "text"),
            None,
        )
        if text is None:
            raise LLMOutputError(PROVIDER_NAME, "response contained no output text", model_name)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMOutputError(
                PROVIDER_NAME,
                f"output did not match {schema.__name__} ({describe_validation_error(exc)})",
                model_name,
            ) from None
