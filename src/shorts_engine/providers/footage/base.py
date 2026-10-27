from abc import ABC, abstractmethod
from typing import Any


class BaseFootageProvider(ABC):
    @abstractmethod
    def search_videos(self, query: str, per_page: int = 1) -> list[dict[str, Any]]:
        """Return video search results matching a visual query."""
