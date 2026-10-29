import json
import logging
import shutil
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Job, Script
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.audio import measure_loudness, probe_audio
from shorts_engine.pipeline.branding import create_branding
from shorts_engine.pipeline.data_graphics import create_graphics
from shorts_engine.pipeline.description import video_title, write_description
from shorts_engine.pipeline.footage import probe_video
from shorts_engine.pipeline.music import prepare_music
from shorts_engine.pipeline.quality import attention_notes, run_quality_checks
from shorts_engine.pipeline.shot_look import best_start, gamma_for, luma_profile
from shorts_engine.pipeline.subtitles import SubtitleError, create_subtitles

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS = 1080, 1920, 30
SHOT_TIMEOUT_S = 300
FINAL_TIMEOUT_S = 900
DURATION_TOLERANCE_S = 0.6
FONTS_SOURCE = Path(__file__).resolve().parents[3] / "assets" / "fonts"


class RenderError(RuntimeError):
    """A render that failed. The message is written for the user; FFmpeg's stderr goes to a log."""

    def __init__(self, job_id: str, message: str):
        super().__init__(f"[{job_id}] render: {message}")
        self.job_id = job_id
        self.stage = "render"


def encoder_args(config: AppConfig, quality: str) -> list[str]:
    """Video encoder options. ``quality`` is "draft" for intermediate shots, "final" for the file."""
    settings = config.settings.get("render", {})
    crf = int(settings.get("crf_draft" if quality == "draft" else "crf_final", 20))
    preset = settings.get("preset", "veryfast")
    if settings.get("encoder", "libx264") == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf), "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def shot_filter(
    blur: int,
    gamma: float,
    frames: int,
    foreground: str = "square",
    zoom_max: float = 1.07,
    portrait: bool = False,
) -> str:
    """One 9:16 shot: the clip over its own blurred copy, brightened if dark, slowly pushing in.

    ``foreground`` "square" crops the middle of a landscape clip to 1080x1080 (more of the
    screen); "wide" keeps the whole landscape frame. The blur is made on a small copy first.
    """
    small_w, small_h = WIDTH // 4, HEIGHT // 4
    lift = f",eq=gamma={gamma}" if gamma > 1.0 else ""
    step = (zoom_max - 1.0) / max(frames, 1)
    if portrait:
        # A vertical clip fills the whole frame: no blurred copy behind it.
        return (
            f"[0:v]fps={FPS},scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase:"
            f"flags=bicubic,crop={WIDTH * 2}:{HEIGHT * 2}{lift},"
            f"zoompan=z='min(zoom+{step:.6f},{zoom_max})':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{HEIGHT}:fps={FPS},"
            f"setsar=1,format=yuv420p[v]"
        )
    if foreground == "wide":
        fg = f"[fg]scale={WIDTH}:-2:force_original_aspect_ratio=decrease{lift},setsar=1[f]"
    else:
        big = WIDTH * 2  # zoom on a larger picture so the push-in moves smoothly
        fg = (
            f"[fg]fps={FPS},scale={big}:{big}:force_original_aspect_ratio=increase:flags=bicubic,"
            f"crop={big}:{big}{lift},"
            f"zoompan=z='min(zoom+{step:.6f},{zoom_max})':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={WIDTH}x{WIDTH}:fps={FPS},setsar=1[f]"
        )
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
        f"crop={small_w}:{small_h}{lift},boxblur={blur}:2,scale={WIDTH}:{HEIGHT},setsar=1[b];"
        f"{fg};"
        f"[b][f]overlay=(W-w)/2:(H-h)/2,fps={FPS},format=yuv420p[v]"
    )


def _ffmpeg(job_id: str, args: list[str], cwd: Path, timeout: int, log: Path) -> None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
            cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise RenderError(job_id, "Pembuatan video terlalu lama dan dihentikan.") from exc
    if result.returncode != 0:
        log.write_text(result.stderr, encoding="utf-8")
        raise RenderError(job_id, "Pembuatan video gagal (detail di render/ffmpeg_error.log).")


def render_shot(job_id: str, job_dir: Path, shot: dict, config: AppConfig) -> tuple[str, dict]:
    """One 9:16 clip of exactly ``needed_s`` seconds; returns its file and how it was looked after.

    A long clip starts at its brightest stretch, a dark one is lifted with gamma, a short one
    repeats. The choices are returned so they can be logged.
    """
    settings = config.settings.get("render", {})
    out = f"render/shot_{shot['shot']}.mp4"
    needed = float(shot["needed_s"])
    looped = bool(shot.get("loop"))
    profile = luma_profile(job_dir / shot["file"])
    start, mean = best_start(profile, needed, float(shot.get("clip_s", needed)))
    if looped:
        start = 0.0
    gamma = gamma_for(
        mean, float(settings.get("luma_target", 55)), float(settings.get("gamma_cap", 1.5))
    )
    graph = shot_filter(
        int(settings.get("blur", 12)),
        gamma,
        round(needed * FPS),
        settings.get("foreground", "square"),
        float(settings.get("zoom_max", 1.07)),
        int(shot.get("height") or 0) > int(shot.get("width") or 0),
    )
    args = ["-ss", f"{start:.2f}", "-i", shot["file"], "-t", f"{needed:.3f}", "-filter_complex",
            graph, "-map", "[v]", "-an", *encoder_args(config, "draft"), out]  # fmt: skip
    if looped:
        args = ["-stream_loop", "-1", *args]
    _ffmpeg(job_id, args, job_dir, SHOT_TIMEOUT_S, job_dir / "render" / "ffmpeg_error.log")
    return out, {
        "shot": shot["shot"],
        "start_s": start,
        "mean_luma": round(mean, 1),
        "gamma": gamma,
    }


def render_video(job_id: str, job_dir: Path, config: AppConfig) -> Path:
    """Shots, narration and subtitles into ``render/final.mp4``, or raise RenderError."""
    plan_file = job_dir / "footage" / "footage_plan.json"
    narration = job_dir / "audio" / "narration.wav"
    subtitles = job_dir / "subtitles" / "subtitles.ass"
    for needed in (plan_file, narration, subtitles):
        if not needed.is_file():
            raise RenderError(job_id, f"Bahan video belum lengkap ({needed.name}).")
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    render_dir = job_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    error_log = render_dir / "ffmpeg_error.log"
    shutil.copytree(FONTS_SOURCE, job_dir / "fonts", dirs_exist_ok=True)

    settings = config.settings.get("render", {})
    fade = float(settings.get("transition_s", 0.25)) if len(plan) > 1 else 0.0
    duration = probe_audio(narration).duration_s
    total = output_length(duration, config)
    tail = total - duration
    rendered = []
    for position, shot in enumerate(plan):
        # each shot but the last is longer by the overlap its transition uses; the last one
        # carries the quiet ending after the voice has finished
        extra = fade if position < len(plan) - 1 else tail
        needed = float(shot["needed_s"]) + extra
        longer = {**shot, "needed_s": needed}
        longer["loop"] = bool(shot.get("loop")) or float(shot.get("clip_s", needed)) < needed
        rendered.append(render_shot(job_id, job_dir, longer, config))
    shots = [name for name, _ in rendered]
    (render_dir / "look.json").write_text(
        json.dumps([info for _, info in rendered], indent=2), encoding="utf-8"
    )

    music = prepare_music(job_id, job_dir, total, config)
    temp, final = render_dir / "final.tmp.mp4", render_dir / "final.mp4"
    video_graph, video_label = join_shots([float(shot["needed_s"]) for shot in plan], fade)
    ending = float(settings.get("fade_out_s", 0.8))
    graph = (
        f"{video_graph};[{video_label}]{caption_filter(job_dir)},"
        f"fade=t=out:st={max(total - ending, 0):.3f}:d={ending}[vout]"
    )
    audio_index = len(shots)
    inputs = [arg for name in shots for arg in ("-i", name)] + ["-i", "audio/narration.wav"]
    if music is not None:
        inputs += ["-i", f"render/{music.name}"]
    graph += ";" + mix_audio(audio_index, config, tail, total, music is not None)
    audio_map = "[aout]"
    _ffmpeg(
        job_id,
        [
            *inputs, "-filter_complex", graph, "-map", "[vout]", "-map", audio_map,
            *encoder_args(config, "final"), "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total:.3f}", "-movflags", "+faststart", "-f", "mp4", f"render/{temp.name}",
        ],
        job_dir, FINAL_TIMEOUT_S, error_log,
    )  # fmt: skip
    check_result(job_id, temp, total)
    temp.replace(final)
    for name in shots:
        (job_dir / name).unlink(missing_ok=True)
    return final


def join_shots(needed: list[float], fade: float) -> tuple[str, str]:
    """Filter graph joining the shot inputs: cross-fades of ``fade`` seconds, or plain cuts.

    Each shot except the last was rendered ``fade`` seconds longer, so the total length is the
    sum of ``needed``. Returns the graph and the label of its output.
    """
    count = len(needed)
    if count == 1:
        return "[0:v]null[joined]", "joined"
    if fade <= 0:
        inputs = "".join(f"[{i}:v]" for i in range(count))
        return f"{inputs}concat=n={count}:v=1:a=0[joined]", "joined"
    steps, previous, elapsed = [], "0:v", 0.0
    for index in range(1, count):
        elapsed += needed[index - 1]
        label = "joined" if index == count - 1 else f"x{index}"
        steps.append(
            f"[{previous}][{index}:v]xfade=transition=fade:duration={fade}:offset={elapsed:.3f}[{label}]"
        )
        previous = label
    return ";".join(steps), "joined"


def output_length(narration_s: float, config: AppConfig) -> float:
    """Video length: the narration plus a quiet ending, so the video does not stop on the last word."""
    return narration_s + float(config.settings.get("render", {}).get("tail_s", 1.2))


def mix_audio(
    narration_index: int, config: AppConfig, tail_s: float, total_s: float, with_music: bool
) -> str:
    """Narration (padded with silence for the ending) over a music bed that eases under the voice.

    The result is labelled [aout] and fades out over the last ``fade_out_s`` seconds.
    """
    render = config.settings.get("render", {})
    settings = config.settings.get("music", {})
    ending = float(render.get("fade_out_s", 0.8))
    fade = f"afade=t=out:st={max(total_s - ending, 0):.3f}:d={ending}"
    padded = f"[{narration_index}:a]apad=pad_dur={tail_s:.3f}"
    if not with_music:
        return f"{padded},{fade}[aout]"
    music = f"[{narration_index + 1}:a]volume={float(settings.get('gain_db', 0))}dB[mus]"
    mix = f"amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.8:level=0,{fade}[aout]"
    if settings.get("duck", True):
        return (
            f"{padded},asplit=2[nar][side];{music};"
            "[mus][side]sidechaincompress=threshold=0.1:ratio=4:attack=30:release=500[duck];"
            f"[nar][duck]{mix}"
        )
    return f"{padded}[nar];{music};[nar][mus]{mix}"


def caption_filter(job_dir: Path) -> str:
    """Burn the data graphics, then the branding, then the captions on top (those that exist)."""
    layers = [
        f"subtitles=subtitles/{name}.ass:fontsdir=fonts"
        for name in ("graphics", "branding", "subtitles")
        if (job_dir / "subtitles" / f"{name}.ass").is_file()
    ]
    return ",".join(layers)


def check_result(job_id: str, video: Path, narration_s: float) -> dict:
    """The rendered file must be 1080x1920, have sound, and last as long as the narration."""
    try:
        width, height, duration = probe_video(video)
        loudness = measure_loudness(video)
    except (ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        raise RenderError(job_id, "Video hasil tidak bisa dibaca atau tidak punya suara.") from exc
    if (width, height) != (WIDTH, HEIGHT):
        raise RenderError(job_id, f"Ukuran video salah ({width}x{height}).")
    if abs(duration - narration_s) > DURATION_TOLERANCE_S:
        raise RenderError(
            job_id,
            f"Durasi video ({duration:.1f} dtk) tidak sama dengan suara ({narration_s:.1f}).",
        )
    return {"width": width, "height": height, "duration_s": duration, **loudness}


def run_rendering(
    db: Session,
    job: Job,
    audio_path: str | None = None,
    config: AppConfig | None = None,
) -> bool:
    config = config or get_config()
    job_dir = get_data_dir() / "jobs" / job.id
    script = db.query(Script).filter(Script.job_id == job.id).first()
    if script is None:
        transition_job_state(db, job, JobState.FAILED, reason="Naskah tidak ditemukan")
        return False
    try:
        # Always rebuilt: a file left from an earlier alignment would show stale times.
        parts = [script.hook, *[s["text"] for s in script.segments], script.ending]
        create_subtitles(job_dir, parts, config)
        create_graphics(
            job_dir, [script.hook, *[s["text"] for s in script.segments], script.ending], config
        )
        length = output_length(probe_audio(job_dir / "audio" / "narration.wav").duration_s, config)
        create_branding(job_dir, video_title(script), length, config)
        final = render_video(job.id, job_dir, config)
        report = check_result(job.id, final, length)
        write_description(db, job, job_dir, config)
        (job_dir / "render" / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        transition_job_state(db, job, JobState.QUALITY_CHECK, reason="Pemeriksaan mutu otomatis")
        notes = attention_notes(run_quality_checks(job_dir, report, length, config, parts))
    except (RenderError, SubtitleError) as exc:
        transition_job_state(db, job, JobState.FAILED, reason=str(exc))
        return False
    except Exception as exc:
        logger.exception("render failed for %s", job.id)
        transition_job_state(db, job, JobState.FAILED, reason=f"Gagal merender: {exc}")
        return False
    reason = "Video selesai, menunggu tontonan Anda"
    if notes:
        reason += f" ({len(notes)} catatan mutu, lihat render/quality.json)"
    transition_job_state(db, job, JobState.PENDING_REVIEW, reason=reason)
    return True
