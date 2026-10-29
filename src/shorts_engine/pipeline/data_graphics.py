import json
import re
from dataclasses import dataclass
from pathlib import Path

from shorts_engine.config import AppConfig
from shorts_engine.pipeline.subtitles import (
    PLAY_RES_X,
    PLAY_RES_Y,
    TimedWord,
    _norm,
    _stamp,
    align_script,
    script_tokens,
)

DEFAULT_UNITS = (
    "miles mile kilometers kilometres km meters metres feet foot degrees degree percent "
    "kg tons pounds mph"
).split()
_QUANTITY = re.compile(r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s+(?P<unit>[A-Za-z%]+)")
_LABEL_AFTER = re.compile(
    r"^\s+(?:in|during)\s+(?:the\s+)?(?P<label>(?:(?:late|early|mid)\s+)?"
    r"(?:\d{4}|\d{1,2}(?:st|nd|rd|th)\s+century))",
    re.IGNORECASE,
)
_NOW_AFTER = re.compile(r"^\s+(?:now|today|currently)\b", re.IGNORECASE)
_BY_BEFORE = re.compile(r"\b[Bb]y\s+(?P<year>\d{4})\b[^.]*$")

# Layout in a 1080x1920 frame: a card in the top band, above the picture.
CARD_X, CARD_Y, CARD_W, CARD_H = 60, 40, 960, 360
LABEL_X, BAR_X, BAR_MAX, BAR_H = 100, 100, 560, 56
LABEL_Y = (85, 250)  # label above its bar
BAR_Y = (150, 315)  # bar centre lines
LABEL_SIZE = 40
GROW_S = 0.6
GREY_BLUE, YELLOW = "&HC8A078&", "&H00D7FF&"


@dataclass
class Quantity:
    value: float
    text: str  # as written in the script, e.g. "24,200 miles"
    label: str
    token_index: int


@dataclass
class Comparison:
    part_index: int
    before: Quantity
    after: Quantity


def _label(sentence_before: str, after: str) -> str:
    match = _LABEL_AFTER.match(after)
    if match:
        return match.group("label").strip().capitalize()
    if _NOW_AFTER.match(after):
        return "Now"
    by = _BY_BEFORE.search(sentence_before)
    return by.group("year") if by else ""


def _token_index(tokens: list[str], number: str) -> int | None:
    wanted = _norm(number)
    return next((i for i, token in enumerate(tokens) if _norm(token) == wanted), None)


def find_comparisons(parts: list[str], units: list[str] | None = None) -> list[Comparison]:
    """A part that states exactly two amounts in the same unit shows them as before and after.

    Only numbers already written in the script are used; nothing is computed or added.
    """
    allowed = {unit.lower() for unit in (units or DEFAULT_UNITS)}
    found: list[Comparison] = []
    for index, part in enumerate(parts):
        tokens = script_tokens([part])
        by_unit: dict[str, list[Quantity]] = {}
        for match in _QUANTITY.finditer(part):
            unit = match.group("unit").lower()
            if unit not in allowed:
                continue
            position = _token_index(tokens, match.group("num"))
            if position is None:
                continue
            sentence_start = part.rfind(".", 0, match.start()) + 1
            after = part[
                match.end() : part.find(".", match.end())
                if "." in part[match.end() :]
                else len(part)
            ]
            quantity = Quantity(
                value=float(match.group("num").replace(",", "")),
                text=f"{match.group('num')} {match.group('unit')}",
                label=_label(part[sentence_start : match.start()], after),
                token_index=position,
            )
            by_unit.setdefault(unit, []).append(quantity)
        for quantities in by_unit.values():
            if len(quantities) == 2 and quantities[0].value > 0 and quantities[1].value > 0:
                found.append(Comparison(index, quantities[0], quantities[1]))
                break
    return found


def _bar_length(value: float, largest: float) -> int:
    return max(8, round(BAR_MAX * value / largest))


def _text(
    start: float,
    end: float,
    x: int,
    y: int,
    size: int,
    text: str,
    align: int,
    fade: str = "200,300",
) -> str:
    tags = f"\\an{align}\\pos({x},{y})\\fs{size}\\1c&HFFFFFF&\\bord0\\shad0\\fad({fade})"
    return f"Dialogue: 2,{_stamp(start)},{_stamp(end)},Card,,0,0,0,,{{{tags}}}{text}"


def _bar(start: float, end: float, y: int, length: int, colour: str) -> str:
    tags = (
        f"\\an7\\pos({BAR_X},{y - BAR_H // 2})\\p1\\1c{colour}\\bord0\\shad0"
        f"\\fscx0\\t(0,{int(GROW_S * 1000)},\\fscx100)\\fad(0,300)"
    )
    shape = f"m 0 0 l {length} 0 {length} {BAR_H} 0 {BAR_H}"
    return f"Dialogue: 1,{_stamp(start)},{_stamp(end)},Card,,0,0,0,,{{{tags}}}{shape}{{\\p0}}"


def build_graphics_ass(
    comparisons: list[Comparison],
    words_by_part: list[list[TimedWord]],
    total_end: float,
    config: AppConfig,
) -> str:
    font = config.settings.get("subtitles", {}).get("font_name", "Poppins")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Card,{font},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    for comparison in comparisons:
        words = words_by_part[comparison.part_index]
        first, second = comparison.before, comparison.after
        t1 = words[first.token_index].start - 0.1
        t2 = words[second.token_index].start - 0.1
        card_start = max(t1 - 0.3, 0.0)
        end = min(max(words[-1].end, t2 + 2.0) + 0.4, total_end)
        largest = max(first.value, second.value)
        card = (
            f"\\an7\\pos({CARD_X},{CARD_Y})\\p1\\1c&H141414&\\1a&H40&\\bord0\\shad0\\fad(250,300)"
        )
        shape = f"m 0 0 l {CARD_W} 0 {CARD_W} {CARD_H} 0 {CARD_H}"
        lines.append(
            f"Dialogue: 0,{_stamp(card_start)},{_stamp(end)},Card,,0,0,0,,{{{card}}}{shape}{{\\p0}}"
        )
        size = max(30, min(46, int(330 / (0.62 * max(len(first.text), len(second.text))))))
        for quantity, row, colour, start in (
            (first, 0, GREY_BLUE, t1),
            (second, 1, YELLOW, t2),
        ):
            length = _bar_length(quantity.value, largest)
            if quantity.label:
                lines.append(
                    _text(card_start, end, LABEL_X, LABEL_Y[row], LABEL_SIZE, quantity.label, 4)
                )
            lines.append(_bar(start, end, BAR_Y[row], length, colour))
            lines.append(
                _text(
                    start + GROW_S,
                    end,
                    BAR_X + length + 20,
                    BAR_Y[row],
                    size,
                    quantity.text,
                    4,
                    "200,300",
                )
            )
    return header + "\n".join(lines) + "\n"


def create_graphics(job_dir: Path, parts: list[str], config: AppConfig) -> Path | None:
    """Write ``subtitles/graphics.ass`` for the comparisons in the script, or remove a stale one."""
    settings = config.settings.get("graphics", {})
    target = job_dir / "subtitles" / "graphics.ass"
    target.unlink(missing_ok=True)
    if not settings.get("enabled", True):
        return None
    comparisons = find_comparisons(parts, settings.get("units"))[
        : int(settings.get("max_per_video", 2))
    ]
    if not comparisons:
        return None
    heard = json.loads((job_dir / "audio" / "alignment.json").read_text(encoding="utf-8"))
    token_groups = [script_tokens([part]) for part in parts]
    words, _ = align_script([t for group in token_groups for t in group], heard)
    words_by_part, cursor = [], 0
    for group in token_groups:
        words_by_part.append(words[cursor : cursor + len(group)])
        cursor += len(group)
    total_end = words[-1].end + 0.3
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        build_graphics_ass(comparisons, words_by_part, total_end, config), encoding="utf-8"
    )
    return target
