import os
from typing import Any

import httpx

from shorts_engine.providers.footage.base import BaseFootageProvider


class PexelsProvider(BaseFootageProvider):
    def __init__(self):
        self.api_key = os.getenv("PEXELS_API_KEY")
        self.base_url = "https://api.pexels.com/videos/search"

    def search_videos(self, query: str, per_page: int = 1) -> list[dict[str, Any]]:
        if not self.api_key:
            raise ValueError("PEXELS_API_KEY tidak ditemukan di environment variables.")

        headers = {"Authorization": self.api_key}
        params = {"query": query, "per_page": per_page, "orientation": "portrait"}

        with httpx.Client(timeout=20.0) as client:
            response = client.get(self.base_url, headers=headers, params=params)
            response.raise_for_status()
            return response.json().get("videos", [])
