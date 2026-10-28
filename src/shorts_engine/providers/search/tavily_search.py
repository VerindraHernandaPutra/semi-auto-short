import logging
import os
from typing import Any

import requests
from tavily import TavilyClient
from tavily.errors import (
    BadRequestError,
    ForbiddenError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

from shorts_engine.config import get_config

from .base import BaseSearchProvider, SearchProviderError, SearchUsage
from .tiers import classify_domain

logger = logging.getLogger(__name__)

PROVIDER_NAME = "tavily"
SEARCH_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 2  # one retry, only for timeouts and network errors


class TavilyProvider(BaseSearchProvider):
    def __init__(self, api_key: str | None = None, client: TavilyClient | None = None):
        key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY")
        if client is None:
            if not key:
                raise SearchProviderError(PROVIDER_NAME, "TAVILY_API_KEY is not set", "auth")
            client = TavilyClient(api_key=key)
        self.client = client
        self.config = get_config()
        self.whitelist = self.config.source_whitelist
        search_settings = self.config.settings.get("search", {})
        self.search_depth = search_settings.get("depth", "basic")
        self.max_results = int(search_settings.get("max_results", 3))
        if self.search_depth not in ("basic", "advanced"):
            raise ValueError(f"search.depth must be basic or advanced, got: {self.search_depth}")

    def _get_tier(self, domain: str) -> str:
        return classify_domain(domain, self.whitelist)

    def _search_once(self, query: str) -> dict[str, Any]:
        """Run one search. Unrecoverable problems raise at once; transient ones retry once."""
        last_problem = "unknown error"
        for _attempt in range(MAX_ATTEMPTS):
            try:
                return self.client.search(
                    query=query,
                    search_depth=self.search_depth,
                    include_raw_content=True,
                    max_results=self.max_results,
                    include_usage=True,
                    timeout=SEARCH_TIMEOUT_S,
                )
            except (InvalidAPIKeyError, MissingAPIKeyError, ForbiddenError):
                raise SearchProviderError(
                    PROVIDER_NAME, "authentication or permission failed", "auth"
                ) from None
            except UsageLimitExceededError:
                raise SearchProviderError(
                    PROVIDER_NAME, "plan usage limit reached", "quota"
                ) from None
            except BadRequestError:
                raise SearchProviderError(
                    PROVIDER_NAME, "request rejected as invalid", "bad_request"
                ) from None
            except (TavilyTimeoutError, requests.RequestException) as exc:
                last_problem = f"network problem ({type(exc).__name__})"
                logger.warning("Tavily search failed temporarily: %s", last_problem)
        raise SearchProviderError(PROVIDER_NAME, last_problem, "network")

    def search_and_extract(self, queries: list[str]) -> list[dict[str, Any]]:
        all_results: list[dict[str, Any]] = []
        failed_queries = 0

        for query in queries:
            try:
                response = self._search_once(query)
            except SearchProviderError as exc:
                if exc.kind != "network":
                    raise  # auth, quota and bad requests are never worth continuing
                failed_queries += 1
                continue

            credits = (response.get("usage") or {}).get("credits")
            if credits is None:
                credits = 2 if self.search_depth == "advanced" else 1
            if self.usage_listener is not None:
                self.usage_listener(SearchUsage(PROVIDER_NAME, "search", int(credits)))

            for result in response.get("results", []):
                url = result.get("url", "")
                # Main domain from the URL
                domain = url.split("/")[2] if "//" in url else url.split("/")[0]
                domain = domain.replace("www.", "")

                all_results.append(
                    {
                        "url": url,
                        "domain": domain,
                        "title": result.get("title", ""),
                        "content": result.get("raw_content") or result.get("content", ""),
                        "tier": self._get_tier(domain),
                    }
                )

        if failed_queries and failed_queries == len(queries):
            raise SearchProviderError(
                PROVIDER_NAME, f"all {failed_queries} searches failed temporarily", "network"
            )
        return all_results
