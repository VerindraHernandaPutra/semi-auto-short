import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base, get_data_dir
from shorts_engine.db.models import Job, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.providers.voice.base import VoiceProviderError
from shorts_engine.services import make as make_module
from shorts_engine.services.make import VideoResult, finish_job, make_videos
from shorts_engine.services.produce import ProduceReport


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Topic(id="t1", text="Why the Moon drifts away", category="space"))
    session.add(Topic(id="t2", text="Why Jupiter shrinks", category="space"))
    session.add(Job(id="job_a", topic_id="t1", status=JobState.AWAITING_VOICE.value))
    session.add(Job(id="job_b", topic_id="t2", status=JobState.AWAITING_VOICE.value))
    session.commit()
    yield session
    session.close()


def stub_stages(monkeypatch, fail_at=None):
    """Replace the three media stages; each one records that it ran."""
    ran = []

    def stage(name):
        def run(db, job, *args, **kwargs):
            ran.append(name)
            if name == fail_at:
                job.error = f"{name} gagal"
                db.commit()  # the real stages save the reason with the state change
                return False
            return True

        return run

    monkeypatch.setattr(make_module, "run_audio_alignment", stage("audio"))
    monkeypatch.setattr(make_module, "run_footage_selection", stage("footage"))
    monkeypatch.setattr(make_module, "run_rendering", stage("render"))
    return ran


def test_a_finished_job_returns_the_video_the_description_and_quality_notes(
    db_session, monkeypatch, tmp_path
):
    ran = stub_stages(monkeypatch)
    render = get_data_dir() / "jobs" / "job_a" / "render"
    render.mkdir(parents=True, exist_ok=True)
    (render / "quality.json").write_text(
        json.dumps(
            [
                {"name": "volume", "status": "ok", "message": "Volume pas."},
                {"name": "gambar", "status": "perhatian", "message": "Ada gambar diam."},
            ]
        )
    )
    job = db_session.get(Job, "job_a")

    result = finish_job(db_session, job, audio_path=tmp_path / "voice.wav", config=get_config())

    assert result.ok and ran == ["audio", "footage", "render"]
    assert result.video == render / "final.mp4" and result.description == render / "description.txt"
    assert result.notes == ["Ada gambar diam."]


def test_a_failing_stage_stops_that_video_with_its_reason_and_skips_the_later_stages(
    db_session, monkeypatch, tmp_path
):
    ran = stub_stages(monkeypatch, fail_at="footage")
    job = db_session.get(Job, "job_a")

    result = finish_job(db_session, job, audio_path=tmp_path / "v.wav", config=get_config())

    assert not result.ok and result.detail == "footage gagal"
    assert ran == ["audio", "footage"]


def test_the_voice_must_come_from_exactly_one_place(db_session, tmp_path):
    job = db_session.get(Job, "job_a")

    both = finish_job(db_session, job, audio_path=tmp_path / "v.wav", clone=True)
    neither = finish_job(db_session, job)

    assert not both.ok and "bukan keduanya" in both.detail
    assert not neither.ok and "bukan keduanya" in neither.detail


def test_a_job_that_is_not_waiting_for_a_voice_is_refused(db_session, tmp_path):
    job = db_session.get(Job, "job_a")
    job.status = JobState.PENDING_REVIEW.value

    result = finish_job(db_session, job, audio_path=tmp_path / "v.wav")

    assert not result.ok and "tidak menunggu suara" in result.detail


def test_a_voice_failure_is_reported_in_plain_words(db_session, monkeypatch):
    def broken(db, job, provider=None):
        raise VoiceProviderError("Lingkungan suara klon belum diatur.")

    monkeypatch.setattr(make_module, "synthesize_narration", broken)
    job = db_session.get(Job, "job_a")

    result = finish_job(db_session, job, clone=True)

    assert not result.ok and result.detail == "Lingkungan suara klon belum diatur."


def test_one_broken_video_does_not_stop_the_next_one(db_session, monkeypatch):
    report = ProduceReport(requested=2, ready_job_ids=["job_a", "job_b"])
    monkeypatch.setattr(make_module, "produce_scripts", lambda db, count, **kw: report)

    def finish(db, job, **kwargs):
        if job.id == "job_a":
            raise RuntimeError("kaboom")
        return VideoResult(job.id, "Why Jupiter shrinks", True)

    monkeypatch.setattr(make_module, "finish_job", finish)

    result = make_videos(db_session, 2, config=get_config())

    assert [v.ok for v in result.videos] == [False, True]
    assert "kaboom" in result.videos[0].detail and result.scripts is report


def test_without_a_voice_only_the_scripts_are_made(db_session, monkeypatch):
    report = ProduceReport(requested=1, ready_job_ids=["job_a"])
    monkeypatch.setattr(make_module, "produce_scripts", lambda db, count, **kw: report)
    monkeypatch.setattr(make_module, "finish_job", lambda *a, **k: pytest.fail("must not run"))

    result = make_videos(db_session, 1, clone=False, config=get_config())

    assert result.videos == [] and result.scripts.ready_job_ids == ["job_a"]
