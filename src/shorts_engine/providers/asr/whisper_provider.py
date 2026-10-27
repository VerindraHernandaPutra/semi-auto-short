from typing import Any

from .base import BaseASRProvider


class WhisperASRProvider(BaseASRProvider):
    def __init__(self, model_name: str = "base"):
        self.model_name = model_name

    def transcribe(self, audio_file_path: str) -> dict[str, Any]:
        import whisper

        model = whisper.load_model(self.model_name)
        return model.transcribe(audio_file_path, word_timestamps=True)
