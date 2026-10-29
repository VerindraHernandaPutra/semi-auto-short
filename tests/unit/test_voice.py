import sys

import pytest

from shorts_engine.pipeline.spoken import to_sentences, to_spoken
from shorts_engine.providers.voice.base import VoiceProviderError
from shorts_engine.providers.voice.chatterbox import ChatterboxVoiceProvider


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("It was seen in 1831.", "It was seen in eighteen thirty-one."),
        ("By 2016, it shrank.", "By twenty sixteen, it shrank."),
        ("About 24,200 miles", "About twenty-four thousand, two hundred miles"),
        ("The late 19th century", "The late nineteenth century"),
        ("It spans 2000 miles", "It spans two thousand miles"),
        ("Over 50 years", "Over fifty years"),
        ("Gains 3.5 percent", "Gains three point five percent"),
        ("Gains 45%", "Gains forty-five percent"),
        ("A 12-23 m/s wind", "A twelve to twenty-three m/s wind"),
    ],
)
def test_numbers_are_written_out_for_the_voice(written, spoken):
    assert to_spoken(written) == spoken


def test_pronunciation_hints_and_brackets_never_reach_the_voice():
    assert to_spoken("Its longitudinal [lon-jih-TOO-dih-nul] extent") == "Its longitudinal extent"


def test_text_is_split_into_one_sentence_per_item():
    parts = ["Hook line.", "First. Second sentence? Third!"]

    assert to_sentences(parts) == ["Hook line.", "First.", "Second sentence?", "Third!"]


STUB = """
import json, sys, wave
request = json.load(open(sys.argv[1], encoding="utf-8"))
if request["sentences"][0] == "fail":
    sys.exit(3)
with wave.open(request["out"], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\\0\\0" * 2400)
"""


@pytest.fixture
def worker(tmp_path):
    path = tmp_path / "stub_worker.py"
    path.write_text(STUB, encoding="utf-8")
    return path


@pytest.fixture
def reference(tmp_path):
    path = tmp_path / "ref.wav"
    path.write_bytes(b"x")
    return path


def provider(worker, reference, **kwargs):
    return ChatterboxVoiceProvider(
        python=kwargs.pop("python", __import__("pathlib").Path(sys.executable)),
        reference=reference,
        worker=worker,
        **kwargs,
    )


def test_a_successful_run_leaves_only_the_final_file(tmp_path, worker, reference):
    out = provider(worker, reference).synthesize(["One.", "Two."], tmp_path / "out" / "voice.wav")

    assert out.is_file() and out.name == "voice.wav"
    assert not list((tmp_path / "out").glob("*.part.wav"))


def test_a_failing_worker_is_reported_in_plain_words_with_a_log(tmp_path, worker, reference):
    with pytest.raises(VoiceProviderError, match="gagal"):
        provider(worker, reference).synthesize(["fail"], tmp_path / "out" / "voice.wav")

    assert (tmp_path / "out" / "voice_error.log").is_file()
    assert not (tmp_path / "out" / "voice.wav").exists()


def test_a_missing_environment_or_reference_is_explained(tmp_path, worker, reference):
    with pytest.raises(VoiceProviderError, match="VOICE_PYTHON"):
        provider(worker, reference, python=None).synthesize(["Hi."], tmp_path / "v.wav")
    with pytest.raises(VoiceProviderError, match="Contoh suara"):
        provider(worker, tmp_path / "none.wav").synthesize(["Hi."], tmp_path / "v.wav")
    with pytest.raises(VoiceProviderError, match="Tidak ada kalimat"):
        provider(worker, reference).synthesize([], tmp_path / "v.wav")


def test_a_run_that_takes_too_long_is_stopped(tmp_path, reference):
    slow = tmp_path / "slow.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    with pytest.raises(VoiceProviderError, match="terlalu lama"):
        provider(slow, reference, timeout_s=1).synthesize(["Hi."], tmp_path / "v.wav")
