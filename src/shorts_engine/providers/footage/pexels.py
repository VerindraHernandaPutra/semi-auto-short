import logging
import os
from typing import Any

import httpx
from pydantic import ValidationError

from .base import BaseFootageProvider, FootageCandidate, FootageProviderError

logger = logging.getLogger(__name__)

PROVIDER_NAME = "pexels"
API_URL = "https://api.pexels.com/v1/videos/search"
MIN_PER_PAGE, MAX_PER_PAGE = 1, 80


class PexelsProvider(BaseFootageProvider):
    """Pexels footage. New API keys are paused by Pexels as of 2026-10-28; keep for later."""

    def __init__(self, api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("PEXELS_API_KEY")
        self.transport = transport

    def search_videos(self, query: str, per_page: int = 1) -> list[FootageCandidate]:
        if not self.api_key:
            raise FootageProviderError(PROVIDER_NAME, "PEXELS_API_KEY is not set")

        params = {
            "query": query,
            "per_page": max(MIN_PER_PAGE, min(per_page, MAX_PER_PAGE)),
            "orientation": "portrait",
        }
        try:
            with httpx.Client(timeout=20.0, transport=self.transport) as client:
                response = client.get(
                    API_URL, headers={"Authorization": self.api_key}, params=params
                )
        except httpx.TransportError as exc:
            raise FootageProviderError(
                PROVIDER_NAME, f"network error ({type(exc).__name__})"
            ) from None
        if response.status_code != 200:
            raise FootageProviderError(
                PROVIDER_NAME,
                f"request rejected (HTTP {response.status_code})",
                response.status_code,
            )
        return self._parse(response.json())

    @staticmethod
    def _parse(payload: dict[str, Any]) -> list[FootageCandidate]:
        candidates: list[FootageCandidate] = []
        for video in payload.get("videos", []):
            files = [f for f in video.get("video_files", []) if f.get("link")]
            if not files:
                continue
            chosen = next((f for f in files if f.get("quality") == "hd"), files[0])
            user = video.get("user") or {}
            name = user.get("name")
            try:
                candidates.append(
                    FootageCandidate(
                        provider=PROVIDER_NAME,
                        asset_id=str(video["id"]),
                        page_url=video["url"],
                        download_url=chosen["link"],
                        thumbnail_url=video.get("image"),
                        width=chosen["width"],
                        height=chosen["height"],
                        duration_s=video["duration"],
                        contributor=name,
                        contributor_url=user.get("url"),
                        attribution=f"Video by {name} on Pexels" if name else "Video from Pexels",
                    )
                )
            except (KeyError, ValidationError):
                logger.warning("Skipping malformed Pexels video")
        return candidates
