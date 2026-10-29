import json

import pytest

from shorts_engine.config import get_config
from shorts_engine.pipeline.subtitles import (
    SubtitleError,
    TimedWord,
    align_script,
    build_ass,
    chunk_words,
    create_subtitles,
    script_tokens,
)


def heard(*pairs):
    return [{"word": w, "start": s, "end": e} for w, s, e in pairs]


def test_hints_are_not_read_or_shown():
    assert script_tokens(["A cephalopod [SEF-uh-luh-pod] swims."]) == ["A", "cephalopod", "swims."]


def test_script_text_is_kept_and_whisper_only_gives_the_time():
    tokens = ["Its", "length", "fell", "from", "24,200", "miles."]
    audio = heard(("Its", 0, 0.3), ("length", 0.3, 0.7), ("fell", 0.7, 1.0), ("from", 1.0, 1.2),
                  ("24", 1.2, 1.7), (",200", 1.7, 2.4), ("miles.", 2.4, 2.9))  # fmt: skip
    words, share = align_script(tokens, audio)

    assert [w.text for w in words] == tokens
    assert share == pytest.approx(5 / 6)
    assert (words[4].start, words[4].end) == (1.2, 2.4)  # one script word spans both fragments


def test_a_misheard_word_still_gets_a_time_between_its_neighbours():
    words, share = align_script(
        ["one", "great", "spot"], heard(("one", 0, 1), ("grape", 1, 2), ("spot", 2, 3))
    )
    assert (words[1].start, words[1].end) == (1, 2)
    assert share == pytest.approx(2 / 3)


def test_a_word_whisper_skipped_shares_the_gap():
    words, _ = align_script(["a", "very", "big"], heard(("a", 0, 1), ("big", 3, 4)))

    assert words[1].start == 1 and words[1].end == 3


def test_times_never_go_backwards():
    tokens = "the quick brown fox jumps over lazy dogs".split()
    audio = heard(*[(t, i * 0.4, i * 0.4 + 0.35) for i, t in enumerate(tokens) if i not in (2, 5)])
    words, _ = align_script(tokens, audio)

    assert all(a.end <= b.start + 1e-9 for a, b in zip(words, words[1:], strict=False))
    assert all(w.end >= w.start for w in words)


def test_chunks_break_at_sentence_ends_and_the_word_limit():
    words = [
        TimedWord(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate("a b. c d e f g h".split())
    ]

    assert [[w.text for w in c] for c in chunk_words(words, 4)] == [
        ["a", "b."], ["c", "d", "e", "f"], ["g", "h"],
    ]  # fmt: skip


def test_ass_highlights_each_word_with_its_duration():
    words = [TimedWord("Hello", 0.0, 0.5), TimedWord("world.", 0.5, 1.2)]
    ass = build_ass(words, get_config())

    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert r"Dialogue: 0,0:00:00.00,0:00:01.50,Main,,0,0,0,,{\k50}Hello {\k70}world." in ass


def test_a_recording_that_ignores_the_script_is_refused(tmp_path):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "alignment.json").write_text(
        json.dumps(heard(("completely", 0, 1), ("different", 1, 2), ("words", 2, 3)))
    )
    with pytest.raises(SubtitleError, match="tidak cocok"):
        create_subtitles(tmp_path, ["Jupiter is a big planet today."], get_config())


def test_subtitles_file_is_written_for_a_matching_recording(tmp_path):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "alignment.json").write_text(
        json.dumps(heard(("Jupiter", 0, 0.6), ("is", 0.6, 0.8), ("big.", 0.8, 1.3)))
    )
    path = create_subtitles(tmp_path, ["Jupiter is big."], get_config())

    assert path.name == "subtitles.ass" and "Jupiter" in path.read_text(encoding="utf-8")
    assert not (tmp_path / "subtitles" / "subtitles.tmp.ass").exists()
