import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .base import BaseLLMProvider, LLMProviderError, LLMUsage

T = TypeVar("T", bound=BaseModel)

PROVIDER_NAME = "anthropic"
DEFAULT_EFFORT = "medium"
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_TOKENS = 16000  # thinking tokens count against this, so keep generous headroom


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
        try:
            response = self.client.messages.parse(
                model=model_name,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
            )
        except anthropic.APIStatusError as exc:
            raise LLMProviderError(
                PROVIDER_NAME, f"request failed (HTTP {exc.status_code}): {exc.message}", model_name
            ) from None
        except anthropic.APIConnectionError as exc:
            raise LLMProviderError(
                PROVIDER_NAME, f"network error ({type(exc).__name__})", model_name
            ) from None
        except ValidationError as exc:
            raise LLMProviderError(
                PROVIDER_NAME,
                f"output did not match {schema.__name__} ({exc.error_count()} errors)",
                model_name,
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
        if response.parsed_output is None:
            raise LLMProviderError(PROVIDER_NAME, "response contained no parsed output", model_name)
        return response.parsed_output
