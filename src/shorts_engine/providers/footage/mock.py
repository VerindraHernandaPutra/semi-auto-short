from .base import BaseFootageProvider, FootageCandidate


class MockFootageProvider(BaseFootageProvider):
    def search_videos(self, query: str, per_page: int = 1) -> list[FootageCandidate]:
        return []
