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
from shorts_engine.pipeline.description import write_description
from shorts_engine.pipeline.footage import probe_video
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


def shot_filter(blur: int) -> str:
    """Landscape clip centred over its own blurred, enlarged copy; the blur is made small first."""
    small_w, small_h = WIDTH // 4, HEIGHT // 4
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={small_w}:{small_h}:force_original_aspect_ratio=increase,"
        f"crop={small_w}:{small_h},boxblur={blur}:2,scale={WIDTH}:{HEIGHT},setsar=1[b];"
        f"[fg]scale={WIDTH}:-2:force_original_aspect_ratio=decrease,setsar=1[f];"
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


def render_shot(job_id: str, job_dir: Path, shot: dict, config: AppConfig) -> str:
    """One 9:16 clip of exactly ``needed_s`` seconds, repeating the source when it is too short."""
    out = f"render/shot_{shot['shot']}.mp4"
    args = ["-i", shot["file"], "-t", f"{shot['needed_s']:.3f}", "-filter_complex", shot_filter(
        int(config.settings.get("render", {}).get("blur", 12))
    ), "-map", "[v]", "-an", *encoder_args(config, "draft"), out]  # fmt: skip
    if shot.get("loop"):
        args = ["-stream_loop", "-1", *args]
    _ffmpeg(job_id, args, job_dir, SHOT_TIMEOUT_S, job_dir / "render" / "ffmpeg_error.log")
    return out


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

    shots = [render_shot(job_id, job_dir, shot, config) for shot in plan]
    concat_list = render_dir / "shots.txt"
    concat_list.write_text(
        "".join(f"file '{Path(name).name}'\n" for name in shots), encoding="utf-8"
    )
    video_only = "render/video_only.mp4"
    _ffmpeg(
        job_id,
        ["-f", "concat", "-safe", "0", "-i", "render/shots.txt", "-c", "copy", video_only],
        job_dir, SHOT_TIMEOUT_S, error_log,
    )  # fmt: skip

    duration = probe_audio(narration).duration_s
    temp, final = render_dir / "final.tmp.mp4", render_dir / "final.mp4"
    _ffmpeg(
        job_id,
        [
            "-i", video_only, "-i", "audio/narration.wav",
            "-vf", "subtitles=subtitles/subtitles.ass:fontsdir=fonts",
            "-map", "0:v:0", "-map", "1:a:0", *encoder_args(config, "final"),
            "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.3f}",
            "-movflags", "+faststart", "-f", "mp4", f"render/{temp.name}",
        ],
        job_dir, FINAL_TIMEOUT_S, error_log,
    )  # fmt: skip
    check_result(job_id, temp, duration)
    temp.replace(final)
    for name in (*shots, video_only):
        (job_dir / name).unlink(missing_ok=True)
    return final


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
        if not (job_dir / "subtitles" / "subtitles.ass").is_file():
            parts = [script.hook, *[s["text"] for s in script.segments], script.ending]
            create_subtitles(job_dir, parts, config)
        final = render_video(job.id, job_dir, config)
        report = check_result(
            job.id, final, probe_audio(job_dir / "audio" / "narration.wav").duration_s
        )
        write_description(db, job, job_dir, config)
        (job_dir / "render" / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    except (RenderError, SubtitleError) as exc:
        transition_job_state(db, job, JobState.FAILED, reason=str(exc))
        return False
    except Exception as exc:
        logger.exception("render failed for %s", job.id)
        transition_job_state(db, job, JobState.FAILED, reason=f"Gagal merender: {exc}")
        return False
    transition_job_state(
        db, job, JobState.PENDING_REVIEW, reason="Video selesai, menunggu tontonan Anda"
    )
    return True
