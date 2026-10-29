import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Job, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.audio import (
    AudioError,
    extract_words,
    measure_loudness,
    prepare_narration,
    probe_audio,
    run_audio_alignment,
)
from shorts_engine.providers.asr.base import BaseASRProvider

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make_recording(path: Path, lead=1.5, speech=4.0, tail=1.5, rate=44100, channels=2) -> Path:
    """Silence, a tone (about -18 dB) standing in for speech, silence."""
    layout = "stereo" if channels == 2 else "mono"
    graph = (
        f"anullsrc=r={rate}:cl={layout}:d={lead}[a];"
        f"sine=f=220:r={rate}:d={speech},aformat=channel_layouts={layout}[b];"
        f"anullsrc=r={rate}:cl={layout}:d={tail}[c];[a][b][c]concat=n=3:v=0:a=1"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-filter_complex", graph, path.name],
        cwd=path.parent, check=True, capture_output=True, timeout=60,
    )  # fmt: skip
    return path


@pytest.fixture
def config():
    cfg = get_config()
    cfg.settings["audio"] = {**cfg.settings["audio"], "min_duration_s": 3, "max_duration_s": 60}
    return cfg


def test_a_recording_becomes_mono_48k_trimmed_and_normalised(tmp_path, config):
    source = make_recording(tmp_path / "take.wav")
    out = prepare_narration("job_1", source, tmp_path / "out", config)

    info = probe_audio(out)
    assert (info.sample_rate, info.channels) == (48000, 1)
    assert 3.5 < info.duration_s < 5.0  # 7 s in, about 4 s of sound kept
    assert -17.5 < measure_loudness(out)["lufs"] < -14.5
    assert not (tmp_path / "out" / "narration.tmp.wav").exists()


def test_a_file_without_sound_is_refused_in_plain_words(tmp_path, config):
    bad = tmp_path / "notes.wav"
    bad.write_text("this is not audio")

    with pytest.raises(AudioError, match="tidak berisi suara"):
        prepare_narration("job_1", bad, tmp_path / "out", config)


def test_a_missing_file_and_wrong_lengths_are_refused(tmp_path, config):
    with pytest.raises(AudioError, match="tidak ditemukan"):
        prepare_narration("job_1", tmp_path / "none.wav", tmp_path / "out", config)
    short = make_recording(tmp_path / "short.wav", lead=0.2, speech=0.5, tail=0.2)
    with pytest.raises(AudioError, match="terlalu pendek"):
        prepare_narration("job_1", short, tmp_path / "out", config)
    config.settings["audio"]["max_duration_s"] = 2
    with pytest.raises(AudioError, match="terlalu panjang"):
        prepare_narration(
            "job_1", make_recording(tmp_path / "long.wav", speech=15), tmp_path / "o2", config
        )


def test_extract_words_flattens_segments():
    result = {"segments": [{"words": [{"word": "a", "start": 0, "end": 1}]}, {"words": []}]}
    assert extract_words(result) == [{"word": "a", "start": 0, "end": 1}]


class FakeASR(BaseASRProvider):
    def __init__(self, words):
        self.words = words

    def transcribe(self, audio_file_path):
        assert audio_file_path.endswith("narration.wav")
        return {"segments": [{"words": self.words}]}


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Topic(id="t1", text="x", category="space"))
    session.add(Job(id="job_a", topic_id="t1", status=JobState.AWAITING_VOICE.value))
    session.commit()
    yield session
    session.close()


def test_alignment_stage_stores_word_times_and_moves_on(db_session, tmp_path, config):
    job = db_session.get(Job, "job_a")
    source = make_recording(tmp_path / "take.wav")
    words = [{"word": "Hello", "start": 0.1, "end": 0.4}]

    assert run_audio_alignment(db_session, job, str(source), FakeASR(words), config) is True
    assert job.status == JobState.SELECTING_FOOTAGE.value


def test_alignment_stage_fails_the_job_when_no_speech_is_heard(db_session, tmp_path, config):
    job = db_session.get(Job, "job_a")
    source = make_recording(tmp_path / "take.wav")

    assert run_audio_alignment(db_session, job, str(source), FakeASR([]), config) is False
    assert job.status == JobState.FAILED.value


def test_speech_recognition_input_is_decoded_to_16k_mono_samples(tmp_path):
    from shorts_engine.providers.asr.whisper_provider import decode_audio

    samples = decode_audio(make_recording(tmp_path / "take.wav"))

    assert samples.dtype.name == "float32"
    assert abs(len(samples) / 16000 - 7.0) < 0.1
