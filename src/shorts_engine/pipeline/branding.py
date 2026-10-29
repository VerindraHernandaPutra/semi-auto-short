from pathlib import Path

from shorts_engine.config import AppConfig
from shorts_engine.pipeline.subtitles import PLAY_RES_X, PLAY_RES_Y, _stamp

POPUP_Y = 1050
WATERMARK_POS = (60, 70)  # top left: clear of the Shorts buttons and of the captions
CHAR_WIDTH = 0.6  # rough width of a Poppins Bold letter as a share of the font size
POP_TEXT_COLOUR = "&H00141414&"  # dark text on the white card


def _escape(text: str) -> str:
    return text.replace("\\", " ").replace("{", "(").replace("}", ")").replace("\n", " ")


def _rounded_rect(width: int, height: int, radius: int) -> str:
    r, w, h = radius, width, height
    return (
        f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} l {w} {h - r} b {w} {h} {w} {h} {w - r} {h} "
        f"l {r} {h} b 0 {h} 0 {h} 0 {h - r} l 0 {r} b 0 0 0 0 {r} 0"
    )


def build_branding_ass(title: str, total_s: float, config: AppConfig) -> str:
    """Title card in the first seconds, a small watermark, and white pop-ups with the channel name."""
    settings = config.settings.get("branding", {})
    font = config.settings.get("subtitles", {}).get("font_name", "Poppins")
    name = _escape(str(settings.get("channel_name", "")).strip())
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{font},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,2,8,90,90,170,1
Style: Brand,{font},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,7,0,0,0,1
Style: Pop,{font},58,{POP_TEXT_COLOUR},&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    title = _escape(" ".join(title.split()))
    if settings.get("title_card", True) and title:
        seconds = float(settings.get("title_seconds", 2.5))
        lines.append(
            f"Dialogue: 3,{_stamp(0.0)},{_stamp(min(seconds, total_s))},Title,,0,0,0,,"
            f"{{\\fad(250,350)}}{title}"
        )
    if name and settings.get("watermark", True):
        x, y = WATERMARK_POS
        lines.append(
            f"Dialogue: 4,{_stamp(0.0)},{_stamp(total_s)},Brand,,0,0,0,,"
            f"{{\\pos({x},{y})\\alpha&H70&}}{name}"
        )
    if name:
        size = 58
        width = int(len(name) * CHAR_WIDTH * size + 110)
        height = 116
        duration_ms = int(float(settings.get("popup_seconds", 2.6)) * 1000)
        out_ms = max(duration_ms - 250, 400)
        for at in settings.get("popup_at_s", [3.0]):
            start = float(at)
            if start >= total_s - 0.5:
                continue
            end = min(start + duration_ms / 1000, total_s)
            pop = (
                f"\\an5\\pos({PLAY_RES_X // 2},{POPUP_Y})\\fscx60\\fscy60\\alpha&HFF&"
                f"\\t(0,200,\\fscx106\\fscy106\\alpha&H00&)\\t(200,330,\\fscx100\\fscy100)"
                f"\\t({out_ms},{duration_ms},\\fscx85\\fscy85\\alpha&HFF&)"
            )
            card = f"{{{pop}\\p1\\1c&HFFFFFF&\\bord0\\shad0}}{_rounded_rect(width, height, 36)}{{\\p0}}"
            lines.append(f"Dialogue: 5,{_stamp(start)},{_stamp(end)},Pop,,0,0,0,,{card}")
            lines.append(f"Dialogue: 6,{_stamp(start)},{_stamp(end)},Pop,,0,0,0,,{{{pop}}}{name}")
    return header + "\n".join(lines) + "\n"


def create_branding(job_dir: Path, title: str, total_s: float, config: AppConfig) -> Path | None:
    """Write ``subtitles/branding.ass``, or remove a stale one when there is nothing to show."""
    target = job_dir / "subtitles" / "branding.ass"
    target.unlink(missing_ok=True)
    settings = config.settings.get("branding", {})
    has_name = bool(str(settings.get("channel_name", "")).strip())
    if not (has_name or (settings.get("title_card", True) and title.strip())):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_branding_ass(title, total_s, config), encoding="utf-8")
    return target
