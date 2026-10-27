from typing import Any

from .base import BaseASRProvider


class MockASRProvider(BaseASRProvider):
    def transcribe(self, audio_file_path: str) -> dict[str, Any]:
        return {"segments": []}
