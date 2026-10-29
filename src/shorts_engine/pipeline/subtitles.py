import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from shorts_engine.config import AppConfig
from shorts_engine.pipeline.script_rules import spoken_text

PLAY_RES_X = 1080
PLAY_RES_Y = 1920
SENTENCE_END = (".", "?", "!")
PAUSE_BREAK_S = 0.5  # a longer pause between words starts a new subtitle
TAIL_S = 0.3  # how long the last subtitle stays after the final word
MIN_MATCH_SHARE = 0.75  # below this the recording does not follow the script


class SubtitleError(RuntimeError):
    """The recording and the script do not fit together. Written for the person who recorded."""


@dataclass
class TimedWord:
    text: str  # as written in the script, with punctuation
    start: float
    end: float


def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def script_tokens(script_parts: list[str]) -> list[str]:
    """Words as they will be read aloud, in order; pronunciation hints removed."""
    return [token for part in script_parts for token in spoken_text(part).split() if _norm(token)]


def align_script(tokens: list[str], heard: list[dict]) -> tuple[list[TimedWord], float]:
    """Give every script word a time, using what Whisper heard only for the timing.

    Whisper may split "24,200" or mishear a word; the script text is what viewers read, so it
    is kept. Returns the timed words and the share of script words that Whisper heard as written.
    """
    heard = [w for w in heard if _norm(w["word"])]
    wanted = [_norm(t) for t in tokens]
    got = [_norm(w["word"]) for w in heard]
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    matched = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, wanted, got, autojunk=False).get_opcodes():
        if tag == "equal":
            matched += i2 - i1
            for offset in range(i2 - i1):  # same word: keep its own time
                word = heard[j1 + offset]
                times[i1 + offset] = (float(word["start"]), float(word["end"]))
        elif j2 > j1 and i2 > i1:
            start, end = float(heard[j1]["start"]), float(heard[j2 - 1]["end"])
            weights = [len(wanted[i]) for i in range(i1, i2)]
            cursor = start
            for offset, weight in enumerate(weights):
                step = (end - start) * weight / sum(weights)
                times[i1 + offset] = (cursor, cursor + step)
                cursor += step

    # Words Whisper did not hear share the gap between their timed neighbours.
    index = 0
    while index < len(tokens):
        if times[index] is not None:
            index += 1
            continue
        run_end = index
        while run_end < len(tokens) and times[run_end] is None:
            run_end += 1
        start = times[index - 1][1] if index > 0 else 0.0  # type: ignore[index]
        end = times[run_end][0] if run_end < len(tokens) else start + 0.3 * (run_end - index)  # type: ignore[index]
        weights = [max(len(wanted[i]), 1) for i in range(index, run_end)]
        cursor = start
        for offset, weight in enumerate(weights):
            step = max(end - start, 0.0) * weight / sum(weights)
            times[index + offset] = (cursor, cursor + step)
            cursor += step
        index = run_end

    words = [TimedWord(t, *span) for t, span in zip(tokens, times, strict=True)]  # type: ignore[misc]
    return words, matched / len(tokens) if tokens else 0.0


def chunk_words(words: list[TimedWord], max_words: int) -> list[list[TimedWord]]:
    """Short groups that fit one subtitle: break at sentence ends, commas, pauses and the limit."""
    chunks: list[list[TimedWord]] = []
    current: list[TimedWord] = []
    for word in words:
        if current and word.start - current[-1].end > PAUSE_BREAK_S:
            chunks.append(current)
            current = []
        current.append(word)
        ends_phrase = word.text.endswith((*SENTENCE_END, ",", ";", ":"))
        if len(current) >= max_words or (ends_phrase and len(current) >= 2):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _stamp(seconds: float) -> str:
    centis = round(max(seconds, 0.0) * 100)
    return (
        f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"
    )


def _escape(text: str) -> str:
    return text.replace("\\", " ").replace("{", "(").replace("}", ")")


def build_ass(words: list[TimedWord], config: AppConfig) -> str:
    """ASS file: each subtitle highlights the word being spoken (karaoke timing)."""
    cfg = config.settings.get("subtitles", {})
    font = cfg.get("font_name", "Poppins")
    size = int(cfg.get("font_size", 76))
    margin_v = int(cfg.get("margin_bottom", 520))
    max_words = int(cfg.get("max_words_per_subtitle", 4))
    # ASS colours are &HAABBGGRR. Karaoke text starts in SecondaryColour, then turns Primary.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{size},&H0000D7FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,90,90,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    chunks = chunk_words(words, max_words)
    lines = []
    for position, chunk in enumerate(chunks):
        start = chunk[0].start
        following = chunks[position + 1][0].start if position + 1 < len(chunks) else None
        end = chunk[-1].end + TAIL_S if following is None else min(chunk[-1].end + 0.25, following)
        end = max(end, start + 0.2)
        parts = []
        for index, word in enumerate(chunk):
            # Highlighted from its own start until the next word starts, so a pause stays with
            # the word before and the highlight never runs ahead of the voice.
            if index + 1 < len(chunk):
                span = chunk[index + 1].start - word.start
            else:
                span = max(word.end - word.start, 0.2)
            parts.append(f"{{\\k{round(max(span, 0.01) * 100)}}}{_escape(word.text)}")
        lines.append(f"Dialogue: 0,{_stamp(start)},{_stamp(end)},Main,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


def create_subtitles(job_dir: Path, script_parts: list[str], config: AppConfig) -> Path:
    """Write ``subtitles/subtitles.ass`` from the script text and ``audio/alignment.json``."""
    heard = json.loads((job_dir / "audio" / "alignment.json").read_text(encoding="utf-8"))
    tokens = script_tokens(script_parts)
    words, share = align_script(tokens, heard)
    if share < MIN_MATCH_SHARE:
        raise SubtitleError(
            f"Rekaman tidak cocok dengan naskah ({share:.0%} kata sama). "
            "Bacalah naskah apa adanya, lalu rekam ulang."
        )
    folder = job_dir / "subtitles"
    folder.mkdir(parents=True, exist_ok=True)
    temp, final = folder / "subtitles.tmp.ass", folder / "subtitles.ass"
    temp.write_text(build_ass(words, config), encoding="utf-8")
    temp.replace(final)
    return final
