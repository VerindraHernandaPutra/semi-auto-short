import numpy as np
import pytest

from shorts_engine.pipeline.quality import (
    Check,
    attention_notes,
    envelope_db,
    speech_starts,
    sync_error,
)


def signal(*parts, rate=16000):
    """('quiet'|'tone', seconds) pieces joined into one signal."""
    pieces = []
    for kind, seconds in parts:
        t = np.arange(int(seconds * rate)) / rate
        pieces.append(
            0.3 * np.sin(2 * np.pi * 220 * t) if kind == "tone" else 0.0005 * np.ones_like(t)
        )
    return np.concatenate(pieces).astype(np.float32)


def test_speech_starts_are_found_after_each_pause():
    samples = signal(("tone", 0.5), ("quiet", 0.6), ("tone", 0.8), ("quiet", 0.5), ("tone", 0.6))

    starts = speech_starts(envelope_db(samples))

    assert starts == pytest.approx([0.0, 1.1, 2.4], abs=0.03)


def test_a_short_breath_is_not_a_new_start():
    samples = signal(("tone", 0.5), ("quiet", 0.1), ("tone", 0.5))

    assert len(speech_starts(envelope_db(samples))) == 1


def test_sync_error_is_the_median_gap_to_the_nearest_word_start():
    speech = [0.0, 1.0, 2.0, 3.0]
    words = [0.0, 1.05, 2.04, 3.06, 3.5]

    assert sync_error(words, speech) == pytest.approx(0.05, abs=0.011)


def test_too_few_pauses_means_the_error_cannot_be_measured():
    assert sync_error([0.0, 1.0], [0.0, 1.0]) is None
    assert sync_error([], [0.0, 1.0, 2.0, 3.0]) is None


def test_only_checks_that_need_a_look_are_reported():
    checks = [
        Check("a", "ok", "baik"),
        Check("b", "perhatian", "periksa ini"),
        Check("c", "tidak terukur", "tak terukur"),
    ]

    assert attention_notes(checks) == ["periksa ini", "tak terukur"]


def heard(text):
    return [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.2} for i, w in enumerate(text.split())]


def test_a_sentence_whose_ending_was_not_heard_is_flagged():
    from shorts_engine.pipeline.quality import unclear_sentences

    parts = ["Saturn has rings.", "Cassini plunged into Saturn's atmosphere on Sept. 15, 2017."]

    flagged = unclear_sentences(
        parts, heard("Saturn has rings. Cassini plunged into Saturn's atmosphere on Sepp.")
    )

    assert flagged == ["Cassini plunged into Saturn's atmosphere on Sept. 15, 2017."]


def test_numbers_heard_as_digits_or_as_words_both_count():
    from shorts_engine.pipeline.quality import unclear_sentences

    parts = ["It ended on Sept. 15, 2017."]

    assert unclear_sentences(parts, heard("It ended on September 15, 2017.")) == []
    assert (
        unclear_sentences(parts, heard("It ended on September fifteenth, twenty seventeen.")) == []
    )


def test_one_misheard_word_is_not_an_alarm():
    from shorts_engine.pipeline.quality import unclear_sentences

    parts = ["Its length has diminished from 24,200 miles in 1879 to around 8,700 miles now."]

    assert (
        unclear_sentences(
            parts,
            heard(
                "Its length has demonized from 24 ,200 miles in 1879 to around 8 ,700 miles now."
            ),
        )
        == []
    )
