from typing import Any

from .base import BaseFootageProvider


class MockFootageProvider(BaseFootageProvider):
    def search_videos(self, query: str, per_page: int = 1) -> list[dict[str, Any]]:
        return []
