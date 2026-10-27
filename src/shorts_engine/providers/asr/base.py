from abc import ABC, abstractmethod
from typing import Any


class BaseASRProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_file_path: str) -> dict[str, Any]:
        """Transcribe audio and return timestamped segments."""
