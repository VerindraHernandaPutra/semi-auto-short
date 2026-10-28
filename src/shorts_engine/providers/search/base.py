from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

SearchErrorKind = Literal["auth", "quota", "bad_request", "network", "unknown"]


class SearchProviderError(Exception):
    """Search failed. Messages never contain keys, URLs or request bodies.

    ``kind`` tells callers whether retrying could ever help: auth, quota and bad_request
    cannot be fixed by retrying, network problems might.
    """

    def __init__(self, provider: str, message: str, kind: SearchErrorKind = "unknown"):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.kind = kind


@dataclass(frozen=True)
class SearchUsage:
    """Credits spent by one search call."""

    provider: str
    operation: str
    credits: int


class BaseSearchProvider(ABC):
    # Called after every billed search, so credits are recorded even if the caller later fails.
    usage_listener: Callable[[SearchUsage], None] | None = None

    @abstractmethod
    def search_and_extract(self, queries: list[str]) -> list[dict[str, Any]]:
        """Search and return extracted page text. Failures raise SearchProviderError.

        An empty list means the searches worked but found nothing, never that a call failed.
        """
        pass
