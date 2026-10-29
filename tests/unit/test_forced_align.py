import numpy as np
import pytest

from shorts_engine.config import get_config
from shorts_engine.db.models import Job, Script, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline import audio as audio_module
from shorts_engine.pipeline.audio import AudioError, refine_with_script
from shorts_engine.pipeline.forced_align import AlignmentError, ctc_align
from shorts_engine.pipeline.spoken import spoken_words
from shorts_engine.pipeline.subtitles import TimedWord, build_ass


def emissions_for(path, vocab=6):
    """Log-probabilities in which frame t strongly prefers token path[t] (0 is the blank)."""
    log_probs = np.full((len(path), vocab), -10.0)
    for frame, token in enumerate(path):
        log_probs[frame, token] = -0.01
    return log_probs


def test_ctc_alignment_finds_the_frames_of_each_letter():
    # blank, A(1) x3, blank x2, B(2) x2, blank, B(2) after a blank, C(3)
    path = [0, 1, 1, 1, 0, 0, 2, 2, 0, 2, 3, 3, 0]

    spans = ctc_align(emissions_for(path), [1, 2, 2, 3])

    assert spans == [(1, 3), (6, 7), (9, 9), (10, 11)]


def test_repeated_letters_need_a_blank_between_them():
    with pytest.raises(AlignmentError):
        ctc_align(emissions_for([1, 1]), [1, 1])  # two identical letters in two frames, no blank


def test_audio_shorter_than_its_text_is_an_error():
    with pytest.raises(AlignmentError, match="shorter"):
        ctc_align(emissions_for([1, 2]), [1, 2, 3])


def test_numbers_are_expanded_into_spoken_words_with_context():
    pairs = dict(spoken_words("It fell from 24,200 miles in 1879 to 2000 miles."))

    assert pairs["24,200"] == ["twenty-four", "thousand,", "two", "hundred"]
    assert pairs["1879"] == ["eighteen", "seventy-nine"]
    assert pairs["2000"] == ["two", "thousand"]  # a count, not the year 2000
    assert pairs["miles."] == ["miles."]


def test_the_highlight_starts_at_each_words_own_start_even_after_a_pause():
    words = [TimedWord("one", 0.0, 0.3), TimedWord("two", 0.4, 0.8), TimedWord("three", 0.9, 1.3)]

    ass = build_ass(words, get_config())

    assert r"{\k40}one {\k50}two {\k40}three" in ass  # the pause stays with "one"


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from shorts_engine.db.base import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Topic(id="t", text="x", category="space"))
    job = Job(id="job_f", topic_id="t", status=JobState.ALIGNING.value)
    session.add(job)
    session.add(
        Script(
            id="s", job_id="job_f", hook="Jupiter is shrinking.",
            segments=[{"text": "It was seen in 1831."}], ending="That is all.",
            structure="x", status="passed",
        )
    )  # fmt: skip
    session.commit()
    yield session, job
    session.close()


HEARD = [
    {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3}
    for i, w in enumerate("Jupiter is shrinking. It was seen in 1831. That is all.".split())
]


def test_script_words_and_forced_times_replace_whispers_words(db_session, monkeypatch, tmp_path):
    session, job = db_session
    forced = [TimedWord(w["word"], w["start"] + 0.05, w["end"]) for w in HEARD]
    monkeypatch.setattr(audio_module, "force_align", lambda path, parts: forced)

    words, method = refine_with_script(session, job, tmp_path / "n.wav", HEARD)

    assert method == "forced" and words[0] == {"word": "Jupiter", "start": 0.05, "end": 0.3}
    assert [w["word"] for w in words][-3:] == ["That", "is", "all."]


def test_a_recording_that_ignores_the_script_is_refused_before_alignment(db_session, tmp_path):
    session, job = db_session
    other = [
        {"word": w, "start": i, "end": i + 0.5}
        for i, w in enumerate("some other text entirely".split())
    ]

    with pytest.raises(AudioError, match="tidak cocok"):
        refine_with_script(session, job, tmp_path / "n.wav", other)


def test_if_alignment_fails_whispers_times_are_kept(db_session, monkeypatch, tmp_path):
    session, job = db_session

    def broken(path, parts):
        raise AlignmentError("model missing")

    monkeypatch.setattr(audio_module, "force_align", broken)

    words, method = refine_with_script(session, job, tmp_path / "n.wav", HEARD)

    assert method == "whisper" and words == HEARD
