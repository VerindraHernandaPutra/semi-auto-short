from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field


class FootageProviderError(Exception):
    """Footage provider failure. Messages never contain URLs, so API keys cannot leak."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status_code = status_code


class FootageCandidate(BaseModel):
    """Provider-independent description of one stock video clip."""

    provider: str
    asset_id: str
    page_url: str
    download_url: str
    thumbnail_url: str | None = None
    width: int
    height: int
    duration_s: float
    size_bytes: int | None = None
    contributor: str | None = None
    contributor_url: str | None = None
    attribution: str
    tags: list[str] = Field(default_factory=list)

    @property
    def orientation(self) -> Literal["portrait", "landscape", "square"]:
        if self.height > self.width:
            return "portrait"
        if self.width > self.height:
            return "landscape"
        return "square"


class BaseFootageProvider(ABC):
    @abstractmethod
    def search_videos(self, query: str, per_page: int = 1) -> list[FootageCandidate]:
        """Return video candidates matching a visual query."""
