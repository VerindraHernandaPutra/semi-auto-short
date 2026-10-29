import json
import shutil
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base, get_data_dir
from shorts_engine.db.models import Job, Script, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.audio import measure_loudness, probe_audio
from shorts_engine.pipeline.footage import probe_video
from shorts_engine.pipeline.render import (
    RenderError,
    check_result,
    encoder_args,
    render_video,
    run_rendering,
)
from shorts_engine.pipeline.subtitles import create_subtitles

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def ffmpeg(cwd, *args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
        cwd=cwd, check=True, capture_output=True, timeout=120,
    )  # fmt: skip


@pytest.fixture
def job_dir(tmp_path):
    """A job folder with a 5 s narration, two short landscape clips, a plan and subtitles."""
    (tmp_path / "audio").mkdir()
    (tmp_path / "footage").mkdir()
    ffmpeg(tmp_path / "audio", "-f", "lavfi", "-i", "sine=f=220:d=5", "-ar", "48000", "-ac", "1",
           "narration.wav")  # fmt: skip
    for i in (1, 2):
        ffmpeg(tmp_path / "footage", "-f", "lavfi", "-i", "testsrc=s=640x360:r=10:d=2",
               "-pix_fmt", "yuv420p", f"c{i}.mp4")  # fmt: skip
    plan = [{"shot": i, "file": f"footage/c{i}.mp4", "needed_s": 2.5, "loop": True} for i in (1, 2)]
    (tmp_path / "footage" / "footage_plan.json").write_text(json.dumps(plan))
    heard = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(
        "Jupiter is shrinking now and slowly".split())]  # fmt: skip
    (tmp_path / "audio" / "alignment.json").write_text(json.dumps(heard))
    create_subtitles(tmp_path, ["Jupiter is shrinking now and slowly"], get_config())
    return tmp_path


def test_render_makes_a_vertical_video_with_sound_as_long_as_the_narration(job_dir):
    final = render_video("job_r", job_dir, get_config())

    width, height, duration = probe_video(final)
    assert (width, height) == (1080, 1920)
    assert duration == pytest.approx(
        probe_audio(job_dir / "audio" / "narration.wav").duration_s, abs=0.3
    )
    assert probe_audio(final).sample_rate > 0  # has an audio stream
    assert not (job_dir / "render" / "final.tmp.mp4").exists()
    assert not list((job_dir / "render").glob("shot_*.mp4"))  # intermediates cleaned up


def test_a_wrong_size_or_length_is_reported_in_plain_words(job_dir):
    ffmpeg(job_dir, "-f", "lavfi", "-i", "testsrc=s=640x360:r=10:d=2", "-f", "lavfi",
           "-i", "sine=f=220:d=2", "-pix_fmt", "yuv420p", "-shortest", "small.mp4")  # fmt: skip
    small = job_dir / "small.mp4"
    with pytest.raises(RenderError, match="tidak punya suara"):
        check_result("job_r", job_dir / "footage" / "c1.mp4", 2.0)

    with pytest.raises(RenderError, match="Ukuran video salah"):
        check_result("job_r", small, 2.0)
    final = render_video("job_r", job_dir, get_config())
    with pytest.raises(RenderError, match="Durasi video"):
        check_result("job_r", final, 20.0)


def test_missing_material_stops_the_render_without_ffmpeg(tmp_path):
    with pytest.raises(RenderError, match="belum lengkap"):
        render_video("job_r", tmp_path, get_config())


def test_encoder_can_be_switched_to_the_gpu():
    config = get_config()
    config.settings["render"] = {**config.settings["render"], "encoder": "h264_nvenc"}

    assert "h264_nvenc" in encoder_args(config, "final")
    config.settings["render"]["encoder"] = "libx264"
    assert "libx264" in encoder_args(config, "final")


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_stage_ends_in_review_when_the_video_is_ready(db_session):
    db_session.add(Topic(id="t", text="x", category="space"))
    job = Job(id="job_s", topic_id="t", status=JobState.RENDERING.value)
    db_session.add(job)
    db_session.add(Script(id="s", job_id="job_s", hook="Jupiter is shrinking now and slowly",
                          segments=[], ending="", structure="x", status="passed"))  # fmt: skip
    db_session.commit()
    folder = get_data_dir() / "jobs" / "job_s"
    (folder / "audio").mkdir(parents=True)
    (folder / "footage").mkdir()
    ffmpeg(
        folder / "audio",
        "-f",
        "lavfi",
        "-i",
        "sine=f=220:d=5",
        "-ar",
        "48000",
        "-ac",
        "1",
        "narration.wav",
    )
    ffmpeg(
        folder / "footage",
        "-f",
        "lavfi",
        "-i",
        "testsrc=s=640x360:r=10:d=2",
        "-pix_fmt",
        "yuv420p",
        "c1.mp4",
    )
    (folder / "footage" / "footage_plan.json").write_text(
        json.dumps([{"shot": 1, "file": "footage/c1.mp4", "needed_s": 5.3, "loop": True}])
    )
    heard = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
             for i, w in enumerate("Jupiter is shrinking now and slowly".split())]  # fmt: skip
    (folder / "audio" / "alignment.json").write_text(json.dumps(heard))

    assert run_rendering(db_session, job) is True
    assert job.status == JobState.PENDING_REVIEW.value
    report = json.loads((folder / "render" / "report.json").read_text())
    assert (report["width"], report["height"]) == (1080, 1920)
    assert -23 < measure_loudness(folder / "render" / "final.mp4")["lufs"] < -5


def test_stage_fails_the_job_with_a_plain_message_when_material_is_missing(db_session):
    db_session.add(Topic(id="t2", text="x", category="space"))
    job = Job(id="job_m", topic_id="t2", status=JobState.RENDERING.value)
    db_session.add(job)
    db_session.add(Script(id="s2", job_id="job_m", hook="Hi there friend now ok",
                          segments=[], ending="", structure="x", status="passed"))  # fmt: skip
    db_session.commit()

    assert run_rendering(db_session, job) is False
    assert job.status == JobState.FAILED.value
