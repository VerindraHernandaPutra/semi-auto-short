import hashlib
import json
import logging
import os
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from shorts_engine.db.base import get_data_dir

from .base import BaseFootageProvider, FootageCandidate, FootageProviderError

logger = logging.getLogger(__name__)

PROVIDER_NAME = "pixabay"
API_URL = "https://pixabay.com/api/videos/"
# Pixabay requires API responses to be cached for 24 hours.
CACHE_TTL_SECONDS = 24 * 60 * 60
RENDITION_PREFERENCE = ("large", "medium", "small", "tiny")
MIN_PER_PAGE, MAX_PER_PAGE = 3, 200  # limits documented for the videos endpoint
MAX_RETRY_WAIT_SECONDS = 60.0


class PixabayProvider(BaseFootageProvider):
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | None = None,
        cache_ttl_s: float = CACHE_TTL_SECONDS,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("PIXABAY_API_KEY")
        self.cache_dir = cache_dir or get_data_dir() / "cache" / PROVIDER_NAME
        self.cache_ttl_s = cache_ttl_s
        self.max_attempts = max_attempts
        self.transport = transport
        self.clock = clock
        self.sleep = sleep

    def search_videos(self, query: str, per_page: int = 1) -> list[FootageCandidate]:
        if not self.api_key:
            raise FootageProviderError(PROVIDER_NAME, "PIXABAY_API_KEY is not set")

        params = {
            "q": query,
            "per_page": max(MIN_PER_PAGE, min(per_page, MAX_PER_PAGE)),
            "safesearch": "true",
        }
        payload = self._read_cache(params)
        if payload is None:
            payload = self._request(params)
            self._write_cache(params, payload)
        return self._parse(payload)

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        last_problem = "unknown error"
        for attempt in range(1, self.max_attempts + 1):
            try:
                with httpx.Client(timeout=20.0, transport=self.transport) as client:
                    response = client.get(API_URL, params={**params, "key": self.api_key})
            except httpx.TransportError as exc:
                # Do not include str(exc): it may carry the request URL, which holds the key.
                last_problem = f"network error ({type(exc).__name__})"
                response = None

            if response is not None:
                status = response.status_code
                if status == 200:
                    return response.json()
                if status not in (429, 500, 502, 503, 504):
                    # 400 means invalid key or parameters; retrying cannot help.
                    raise FootageProviderError(
                        PROVIDER_NAME, f"request rejected (HTTP {status})", status
                    )
                last_problem = f"temporary error (HTTP {status})"
                retry_after = response.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    wait = min(float(retry_after), MAX_RETRY_WAIT_SECONDS)
                else:
                    wait = min(2.0**attempt + random.uniform(0, 1), MAX_RETRY_WAIT_SECONDS)
            else:
                wait = min(2.0**attempt + random.uniform(0, 1), MAX_RETRY_WAIT_SECONDS)

            if attempt < self.max_attempts:
                logger.warning("Pixabay %s; retrying in %.1fs", last_problem, wait)
                self.sleep(wait)
        raise FootageProviderError(
            PROVIDER_NAME, f"gave up after {self.max_attempts} attempts: {last_problem}"
        )

    def _cache_path(self, params: dict[str, Any]) -> Path:
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, params: dict[str, Any]) -> dict[str, Any] | None:
        path = self._cache_path(params)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if self.clock() - float(entry["fetched_at"]) < self.cache_ttl_s:
                return entry["payload"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _write_cache(self, params: dict[str, Any], payload: dict[str, Any]) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            entry = {"fetched_at": self.clock(), "payload": payload}
            self._cache_path(params).write_text(json.dumps(entry), encoding="utf-8")
        except OSError:
            logger.warning("Could not write Pixabay cache; continuing without it")

    @staticmethod
    def _is_ai_generated(hit: dict[str, Any]) -> bool:
        tags = {t.strip().lower() for t in str(hit.get("tags", "")).split(",")}
        return "ai generated" in tags

    @classmethod
    def _parse(cls, payload: dict[str, Any]) -> list[FootageCandidate]:
        candidates: list[FootageCandidate] = []
        for hit in payload.get("hits", []):
            # A fact-checked science channel must not show synthetic animals.
            if cls._is_ai_generated(hit):
                continue
            rendition = next(
                (
                    hit["videos"][name]
                    for name in RENDITION_PREFERENCE
                    if hit.get("videos", {}).get(name, {}).get("url")
                ),
                None,
            )
            if rendition is None:
                continue
            user = hit.get("user")
            try:
                candidates.append(
                    FootageCandidate(
                        provider=PROVIDER_NAME,
                        asset_id=str(hit["id"]),
                        page_url=hit["pageURL"],
                        download_url=rendition["url"],
                        thumbnail_url=rendition.get("thumbnail") or None,
                        width=rendition["width"],
                        height=rendition["height"],
                        duration_s=hit["duration"],
                        size_bytes=rendition.get("size") or None,
                        contributor=user,
                        attribution=f"Video by {user} from Pixabay"
                        if user
                        else "Video from Pixabay",
                        tags=[t.strip() for t in str(hit.get("tags", "")).split(",") if t.strip()],
                    )
                )
            except (KeyError, ValidationError):
                logger.warning("Skipping malformed Pixabay hit")
        return candidates
