from typing import Any

from .base import BaseSearchProvider

# Prose lines of at least eight words, like an article body, so the page cleaning keeps them.
_ARTICLE = (
    "This is mock article text written to look like a real scientific explanation page.\n"
    "Each line is a full sentence so that the navigation filter keeps it as prose.\n"
    "It is used only by dry runs and tests, never by a real production run.\n"
    "The pipeline must still refuse to verify facts from these pages, by design.\n"
    "Mock sources exist to exercise the wiring of the research stage without cost.\n"
    "A real run reads pages that a real search provider returned for real queries.\n"
    "The text repeats so the page is long enough to pass the minimum length check.\n"
)


class MockSearchProvider(BaseSearchProvider):
    """Two tier A pages from independent domains, offline and free."""

    def search_and_extract(self, queries: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "url": f"https://www.{domain}/mock-article",
                "domain": domain,
                "title": f"Mock article on {domain}",
                "content": _ARTICLE * 2,
                "tier": "tier_a",
            }
            for domain in ("nature.com", "nasa.gov")
        ]
