import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import BaseASRProvider

SAMPLE_RATE = 16000
DECODE_TIMEOUT_S = 120


def decode_audio(path: Path):
    """Mono 16 kHz float samples decoded with FFmpeg.

    faster-whisper decodes with PyAV, whose newer releases break its call; FFmpeg is already
    required by the project and gives the same samples.
    """
    import numpy as np

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path.name,
         "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        cwd=path.parent, capture_output=True, timeout=DECODE_TIMEOUT_S,
    )  # fmt: skip
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("audio could not be decoded for speech recognition")
    return np.frombuffer(result.stdout, dtype=np.float32)


def add_cuda_library_paths() -> list[Path]:
    """Make the pip-installed NVIDIA libraries (cuBLAS, cuDNN) loadable on Windows.

    CTranslate2 looks for their DLLs on PATH; the wheels put them under site-packages/nvidia.
    Returns the directories added, which is empty when the packages are not installed.
    """
    added: list[Path] = []
    for base in map(Path, sys.path):
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            added.append(bin_dir)
    return added


class WhisperASRProvider(BaseASRProvider):
    """faster-whisper: word timestamps on the GPU when CUDA works, otherwise on the CPU."""

    def __init__(
        self,
        model_name: str = "small.en",
        device: str = "auto",
        compute_type: str | None = None,
        language: str = "en",
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.used_device: str | None = None

    def _load(self):
        from faster_whisper import WhisperModel

        add_cuda_library_paths()
        attempts = ["cuda", "cpu"] if self.device == "auto" else [self.device]
        last_error: Exception | None = None
        for device in attempts:
            compute = self.compute_type or ("float16" if device == "cuda" else "int8")
            try:
                model = WhisperModel(self.model_name, device=device, compute_type=compute)
            except Exception as exc:  # CUDA missing or unusable: fall back, but never hide it
                last_error = exc
                continue
            self.used_device = device
            return model
        raise RuntimeError(f"Whisper model could not be loaded: {last_error}")

    def transcribe(self, audio_file_path: str) -> dict[str, Any]:
        samples = decode_audio(Path(audio_file_path))
        model = self._load()
        try:
            segments, _info = model.transcribe(
                samples, language=self.language, word_timestamps=True, beam_size=5
            )
            return {
                "segments": [
                    {
                        "text": segment.text,
                        "words": [
                            {"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                            for w in (segment.words or [])
                        ],
                    }
                    for segment in segments
                ]
            }
        except RuntimeError:
            if self.used_device != "cuda" or self.device != "auto":
                raise
            # cuDNN/cuBLAS can fail only when the first batch runs; retry once on the CPU.
            self.device = "cpu"
            return self.transcribe(audio_file_path)
