import json
import subprocess
from pathlib import Path

from .base import BaseVoiceProvider, VoiceProviderError

WORKER = Path(__file__).resolve().parents[4] / "tools" / "voice_worker.py"


class ChatterboxVoiceProvider(BaseVoiceProvider):
    """Clones the user's voice with Chatterbox (MIT), run by a separate Python environment.

    The model needs torch with CUDA (~2.5 GB), so it lives outside this project's dependencies.
    ``python`` is that environment's interpreter.
    """

    def __init__(
        self,
        python: Path | None,
        reference: Path,
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        gap_s: float = 0.35,
        timeout_s: int = 3600,
        worker: Path = WORKER,
    ):
        self.python = python
        self.reference = reference
        self.exaggeration = exaggeration
        self.cfg_weight = cfg_weight
        self.gap_s = gap_s
        self.timeout_s = timeout_s
        self.worker = worker

    def synthesize(self, sentences: list[str], out_path: Path) -> Path:
        if self.python is None or not self.python.is_file():
            raise VoiceProviderError(
                "Lingkungan suara klon belum diatur. Isi VOICE_PYTHON di file .env."
            )
        if not self.reference.is_file():
            raise VoiceProviderError("Contoh suara Anda tidak ditemukan.")
        if not sentences:
            raise VoiceProviderError("Tidak ada kalimat untuk diucapkan.")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        temp = out_path.with_suffix(".part.wav")
        request = out_path.parent / "voice_request.json"
        request.write_text(
            json.dumps(
                {
                    "reference": str(self.reference.resolve()),
                    "out": temp.name,
                    "sentences": sentences,
                    "exaggeration": self.exaggeration,
                    "cfg_weight": self.cfg_weight,
                    "gap_s": self.gap_s,
                }
            ),
            encoding="utf-8",
        )
        log = out_path.parent / "voice_error.log"
        try:
            result = subprocess.run(
                [str(self.python), "-I", str(self.worker), request.name],
                cwd=out_path.parent,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired as exc:
            temp.unlink(missing_ok=True)
            raise VoiceProviderError("Pembuatan suara terlalu lama dan dihentikan.") from exc
        if result.returncode != 0 or not temp.is_file():
            log.write_text(result.stderr[-4000:], encoding="utf-8")
            temp.unlink(missing_ok=True)
            raise VoiceProviderError("Pembuatan suara gagal (detail di voice_error.log).")
        temp.replace(out_path)
        return out_path
