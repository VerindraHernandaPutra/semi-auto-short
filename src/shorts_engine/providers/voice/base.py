from abc import ABC, abstractmethod
from pathlib import Path


class VoiceProviderError(RuntimeError):
    """Voice synthesis failed. The message is written for the user and holds no paths to secrets."""


class BaseVoiceProvider(ABC):
    @abstractmethod
    def synthesize(self, sentences: list[str], out_path: Path) -> Path:
        """Speak the sentences in order into one audio file at ``out_path``."""
