import hashlib
import subprocess
from pathlib import Path

from shorts_engine.config import AppConfig

MUSIC_TIMEOUT_S = 180
TRACK_SUFFIXES = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
TRACKS_DIR = Path(__file__).resolve().parents[3] / "assets" / "music"
FADE_IN_S, FADE_OUT_S = 2.0, 3.0

# A soft A-minor pad (A3 E4 A4 C5 E5), left and right slightly detuned for width. It sits above
# 200 Hz because phone speakers barely reproduce lower notes. It is made here from sine waves,
# so it carries no licence of anyone else.
_NOTES = (220.0, 329.63, 440.0, 523.25, 659.25)
_WEIGHTS = (0.22, 0.18, 0.14, 0.09, 0.07)


def _pad_expression(detune: float) -> str:
    return "+".join(
        f"{w}*sin(2*PI*{f * detune:.3f}*t)" for f, w in zip(_NOTES, _WEIGHTS, strict=True)
    )


def ambient_filter(duration_s: float) -> list[str]:
    """FFmpeg arguments that synthesise ``duration_s`` seconds of soft ambient pad."""
    graph = (
        f"aevalsrc='{_pad_expression(1.0)}|{_pad_expression(1.004)}':s=48000:d={duration_s:.3f},"
        f"tremolo=f=0.15:d=0.3,lowpass=f=3500,aecho=0.8:0.6:70|130:0.35|0.25"
    )
    return ["-f", "lavfi", "-i", graph]


def user_tracks() -> list[Path]:
    """Tracks the user put in assets/music (licences are the user's to check; not committed)."""
    if not TRACKS_DIR.is_dir():
        return []
    return sorted(p for p in TRACKS_DIR.iterdir() if p.suffix.lower() in TRACK_SUFFIXES)


def pick_track(job_id: str, tracks: list[Path]) -> Path | None:
    """The same job always gets the same track; different jobs spread over the tracks."""
    if not tracks:
        return None
    index = int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % len(tracks)
    return tracks[index]


def prepare_music(job_id: str, job_dir: Path, duration_s: float, config: AppConfig) -> Path | None:
    """Write ``render/music.wav`` (stereo 48 kHz, as long as the video, faded) or return None.

    mode "off": no music. "ambient": the synthesised pad. "tracks": a track from assets/music,
    falling back to the pad when the folder is empty.
    """
    mode = config.settings.get("music", {}).get("mode", "ambient")
    if mode == "off":
        return None
    out_dir = job_dir / "render"
    out_dir.mkdir(parents=True, exist_ok=True)
    temp, final = out_dir / "music.tmp.wav", out_dir / "music.wav"
    track = pick_track(job_id, user_tracks()) if mode == "tracks" else None
    # Level first, fades after: loudnorm would lift a fade-out back up if it ran last.
    target = float(config.settings.get("music", {}).get("target_lufs", -23))
    level = f"loudnorm=I={target}:TP=-3:LRA=7"
    fades = (
        f"afade=t=in:d={FADE_IN_S},"
        f"afade=t=out:st={max(duration_s - FADE_OUT_S, 0):.3f}:d={FADE_OUT_S}"
    )
    if track is not None:
        source = ["-stream_loop", "-1", "-i", str(track.resolve())]
        chain = [
            "-af", f"aformat=channel_layouts=stereo,{level},{fades}", "-t", f"{duration_s:.3f}",
        ]  # fmt: skip
    else:
        source, chain = ambient_filter(duration_s), ["-af", f"{level},{fades}"]
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-y", *source, *chain, "-ar", "48000",
             "-c:a", "pcm_s16le", temp.name],
            cwd=out_dir, capture_output=True, text=True, timeout=MUSIC_TIMEOUT_S, encoding="utf-8",
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        temp.unlink(missing_ok=True)
        return None
    if result.returncode != 0 or not temp.is_file():
        temp.unlink(missing_ok=True)
        return None  # no music is better than a failed video
    temp.replace(final)
    return final
