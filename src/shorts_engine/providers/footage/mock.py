from .base import BaseFootageProvider, FootageCandidate


class MockFootageProvider(BaseFootageProvider):
    """Returns clearly fake clips so the pipeline can run without network or API keys."""

    def search_videos(self, query: str, per_page: int = 1) -> list[FootageCandidate]:
        return [
            FootageCandidate(
                provider="mock",
                asset_id=f"mock-{abs(hash(query)) % 10_000}-{index}",
                page_url="https://example.invalid/mock",
                download_url="mock_video.mp4",
                width=1920,
                height=1080,
                duration_s=10.0,
                attribution="Mock footage",
            )
            for index in range(min(max(per_page, 1), 5))
        ]
