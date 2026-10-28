from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProviderError(Exception):
    """LLM call failed. Messages never contain secrets or request bodies."""

    def __init__(self, provider: str, message: str, model: str | None = None):
        super().__init__(f"[{provider}{':' + model if model else ''}] {message}")
        self.provider = provider
        self.model = model


@dataclass(frozen=True)
class LLMUsage:
    """Token usage of one LLM call; ``operation`` is the output schema name."""

    provider: str
    model: str
    operation: str
    input_tokens: int
    output_tokens: int


class BaseLLMProvider(ABC):
    # Called after every billed call, so costs are recorded even if the caller later fails.
    usage_listener: Callable[[LLMUsage], None] | None = None

    @abstractmethod
    def generate_structured(
        self, prompt: str, schema: type[T], model_name: str, effort: str | None = None
    ) -> T:
        """Return output validated against a Pydantic schema; invalid output is an error.

        ``effort`` is a hint for how much the model should think (low, medium, high);
        providers that have no such control ignore it.
        """
