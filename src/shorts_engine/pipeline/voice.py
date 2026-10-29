from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Job, Script
from shorts_engine.pipeline.spoken import to_sentences
from shorts_engine.providers.factory import get_voice_provider
from shorts_engine.providers.voice.base import BaseVoiceProvider, VoiceProviderError


def synthesize_narration(db: Session, job: Job, provider: BaseVoiceProvider | None = None) -> Path:
    """Speak the job's script in the cloned voice into ``audio/voice_clone.wav``.

    The job stays in its state: the result is a recording like any other and goes through the
    audio stage next. Raises VoiceProviderError with a message for the user.
    """
    script = db.query(Script).filter(Script.job_id == job.id).first()
    if script is None:
        raise VoiceProviderError("Naskah tidak ditemukan.")
    parts = [script.hook, *[segment["text"] for segment in script.segments], script.ending]
    audio_dir = get_data_dir() / "jobs" / job.id / "audio"
    out = (provider or get_voice_provider()).synthesize(
        to_sentences(parts), audio_dir / "voice_clone.wav"
    )
    (audio_dir / "voice_source.txt").write_text("clone", encoding="utf-8")
    return out
