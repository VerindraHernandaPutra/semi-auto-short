"""From a queued topic to a finished video, with nothing asked of the user on the way.

``finish_job`` takes a job that has a script and turns it into a video (voice, subtitles, footage,
render, quality checks). ``make_videos`` first produces the scripts and then finishes each one. A
step that fails stops that video only; the others still get made.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Job, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.audio import run_audio_alignment
from shorts_engine.pipeline.footage import run_footage_selection
from shorts_engine.pipeline.render import run_rendering
from shorts_engine.pipeline.voice import synthesize_narration
from shorts_engine.providers.voice.base import BaseVoiceProvider, VoiceProviderError
from shorts_engine.services.produce import ProduceReport, produce_scripts

logger = logging.getLogger(__name__)


@dataclass
class VideoResult:
    job_id: str
    topic: str
    ok: bool
    video: Path | None = None
    description: Path | None = None
    notes: list[str] = field(default_factory=list)  # quality checks that need a look
    detail: str = ""  # why it stopped, in plain Indonesian


@dataclass
class MakeReport:
    scripts: ProduceReport
    videos: list[VideoResult] = field(default_factory=list)


def _topic_text(db: Session, job: Job) -> str:
    topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
    return topic.text if topic else ""


def _quality_notes(render_dir: Path) -> list[str]:
    quality_file = render_dir / "quality.json"
    if not quality_file.is_file():
        return []
    checks = json.loads(quality_file.read_text(encoding="utf-8"))
    return [item["message"] for item in checks if item["status"] != "ok"]


def finish_job(
    db: Session,
    job: Job,
    audio_path: Path | None = None,
    clone: bool = False,
    voice: BaseVoiceProvider | None = None,
    config: AppConfig | None = None,
) -> VideoResult:
    """Voice, subtitles, footage, render and checks for a job that is waiting for its voice."""
    config = config or get_config()
    topic = _topic_text(db, job)
    if job.status != JobState.AWAITING_VOICE.value:
        return VideoResult(
            job.id, topic, False, detail=f"Job tidak menunggu suara (status: {job.status})."
        )
    if clone == (audio_path is not None):
        return VideoResult(
            job.id, topic, False, detail="Pilih rekaman atau suara klon, bukan keduanya."
        )

    if clone:
        try:
            audio_path = synthesize_narration(db, job, provider=voice)
        except VoiceProviderError as exc:
            return VideoResult(job.id, topic, False, detail=str(exc))

    stages = (
        lambda: run_audio_alignment(db, job, str(audio_path), config=config),
        lambda: run_footage_selection(db, job, config=config),
        lambda: run_rendering(db, job, config=config),
    )
    for stage in stages:
        if not stage():
            db.refresh(job)
            return VideoResult(job.id, topic, False, detail=job.error or "Tahap gagal.")

    render_dir = get_data_dir() / "jobs" / job.id / "render"
    return VideoResult(
        job.id,
        topic,
        True,
        video=render_dir / "final.mp4",
        description=render_dir / "description.txt",
        notes=_quality_notes(render_dir),
    )


def make_videos(
    db: Session,
    count: int,
    clone: bool = True,
    voice: BaseVoiceProvider | None = None,
    config: AppConfig | None = None,
    **producers,
) -> MakeReport:
    """Produce ``count`` scripts (replacing dropped topics) and make a video of each.

    ``producers`` are passed to ``produce_scripts`` (providers for tests). Without ``clone`` the
    scripts are left waiting for a recording.
    """
    config = config or get_config()
    scripts = produce_scripts(db, count, config=config, **producers)
    report = MakeReport(scripts=scripts)
    if not clone:
        return report
    for job_id in scripts.ready_job_ids:
        job = db.query(Job).filter(Job.id == job_id).one()
        try:
            report.videos.append(finish_job(db, job, clone=True, voice=voice, config=config))
        except Exception as exc:  # one broken video must not stop the next one
            logger.exception("making the video for %s failed", job_id)
            report.videos.append(
                VideoResult(job_id, _topic_text(db, job), False, detail=f"Galat tak terduga: {exc}")
            )
    return report
