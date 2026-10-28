import os
from typing import Any

from tavily import TavilyClient

from shorts_engine.config import get_config

from .base import BaseSearchProvider
from .tiers import classify_domain


class TavilyProvider(BaseSearchProvider):
    def __init__(self):
        self.api_key = os.getenv("TAVILY_API_KEY")
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY tidak ditemukan di environment variables.")
        self.client = TavilyClient(api_key=self.api_key)
        self.config = get_config()
        self.whitelist = self.config.source_whitelist
        search_settings = self.config.settings.get("search", {})
        self.search_depth = search_settings.get("depth", "basic")
        self.max_results = int(search_settings.get("max_results", 3))
        if self.search_depth not in ("basic", "advanced"):
            raise ValueError(f"search.depth must be basic or advanced, got: {self.search_depth}")

    def _get_tier(self, domain: str) -> str:
        return classify_domain(domain, self.whitelist)

    def search_and_extract(self, queries: list[str]) -> list[dict[str, Any]]:
        all_results = []

        for query in queries:
            try:
                response = self.client.search(
                    query=query,
                    search_depth=self.search_depth,
                    include_raw_content=True,
                    max_results=self.max_results,
                )

                for result in response.get("results", []):
                    url = result.get("url", "")
                    # Ambil domain utama dari URL
                    domain = url.split("/")[2] if "//" in url else url.split("/")[0]
                    domain = domain.replace("www.", "")

                    tier = self._get_tier(domain)

                    all_results.append(
                        {
                            "url": url,
                            "domain": domain,
                            "title": result.get("title", ""),
                            "content": result.get("raw_content", result.get("content", "")),
                            "tier": tier,
                        }
                    )
            except Exception as e:
                print(f"Error saat mencari '{query}': {e}")

        return all_results
