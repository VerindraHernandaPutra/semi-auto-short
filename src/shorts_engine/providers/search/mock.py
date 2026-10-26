from typing import List, Dict, Any
from .base import BaseSearchProvider

class MockSearchProvider(BaseSearchProvider):
    def search_and_extract(self, queries: List[str]) -> List[Dict[str, Any]]:
        return [
            {
                "url": "https://www.nature.com/mock-article",
                "domain": "nature.com",
                "title": "Mock Nature Article",
                "content": "This is mock extracted text from a Tier A scientific source. It supports the claims.",
                "tier": "tier_a"
            }
        ]