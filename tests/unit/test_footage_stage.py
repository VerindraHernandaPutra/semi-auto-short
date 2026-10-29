import json
import shutil
import subprocess

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base, get_data_dir
from shorts_engine.db.models import Job, Script, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.footage import (
    FootageError,
    Shot,
    content_words,
    download_clip,
    pick_footage,
    query_attempts,
    relevance,
    run_footage_selection,
    shot_durations,
)
from shorts_engine.providers.footage.base import BaseFootageProvider, FootageCandidate

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
HOSTS = ["cdn.example"]


def cand(asset_id, tags, duration=10.0, size=None):
    return FootageCandidate(
        provider="fake",
        asset_id=asset_id,
        page_url=f"https://fake/{asset_id}",
        download_url=f"https://cdn.example/{asset_id}.mp4",
        width=1920,
        height=1080,
        duration_s=duration,
        size_bytes=size,
        attribution=f"Video {asset_id}",
        tags=tags,
    )


class FakeProvider(BaseFootageProvider):
    def __init__(self, by_query):
        self.by_query = by_query

    def search_videos(self, query, per_page=1):
        return self.by_query.get(query, [])


@pytest.fixture(scope="module")
def mp4_bytes(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clip")
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "lavfi",
            "-i", "color=c=blue:s=320x180:r=10:d=2", "-pix_fmt", "yuv420p", "c.mp4",
        ],
        cwd=folder,
        check=True,
        capture_output=True,
        timeout=60,
    )  # fmt: skip
    return (folder / "c.mp4").read_bytes()


def serving(body: bytes, status=200):
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


def test_queries_go_from_specific_to_general_and_stay_short():
    attempts = query_attempts(
        "Jupiter with its red storm, seen through a telescope at night",
        "Jupiter's Great Red Spot",
        ["space"],
    )

    assert attempts[0] == "jupiter red storm"
    assert attempts[-1] == "space" and all(len(a.split()) <= 3 for a in attempts)
    assert content_words("The Great Red Spot") == ["great", "red", "spot"]


def test_fiction_tags_disqualify_and_overlap_scores():
    wanted = {"jupiter", "storm"}

    assert relevance(cand("a", ["jupiter", "storm"]), wanted, {"fantasy"}) == 2
    assert relevance(cand("b", ["jupiter", "fantasy"]), wanted, {"fantasy"}) == -1


def test_best_tagged_clip_wins_and_no_clip_repeats():
    strong = cand("strong", ["jupiter", "storm", "planet"])
    provider = FakeProvider({"jupiter storm": [cand("weak", ["jupiter"]), strong]})
    settings = {"min_clip_seconds": 5, "avoid_tags": []}
    shots = [Shot(0, "jupiter storm", 8.0), Shot(1, "jupiter storm", 8.0)]

    picks = pick_footage(provider, shots, "Jupiter", settings)

    assert [p.candidate.asset_id for p in picks] == ["strong", "weak"]


def test_short_clips_are_skipped_and_a_shorter_than_needed_clip_is_marked_to_loop():
    provider = FakeProvider({"space": [cand("tiny", ["space"], 3.0), cand("ok", ["space"], 6.0)]})
    settings = {"min_clip_seconds": 5, "fallback_queries": ["space"]}

    [pick] = pick_footage(provider, [Shot(0, "", 12.0)], "x", settings)

    assert pick.candidate.asset_id == "ok" and pick.loop is True


def test_a_shot_with_nothing_suitable_is_none_and_oversized_files_are_skipped():
    provider = FakeProvider({"space": [cand("huge", ["space"], 10.0, size=900 * 1024 * 1024)]})
    settings = {"fallback_queries": ["space"], "max_clip_mb": 60}

    assert pick_footage(provider, [Shot(0, "", 5.0)], "x", settings) == [None]


def test_download_accepts_a_playable_clip_from_an_allowed_host(tmp_path, mp4_bytes):
    path = download_clip(
        "https://cdn.example/a.mp4", tmp_path / "a.mp4", HOSTS, 10_000_000, serving(mp4_bytes)
    )

    assert path.exists() and not (tmp_path / "a.part").exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.example/a.mp4",
        "https://evil.example/a.mp4",
        "https://cdn.example.evil.com/a.mp4",
    ],
)
def test_download_refuses_other_hosts_and_plain_http(tmp_path, url, mp4_bytes):
    with pytest.raises(FootageError, match="tidak diizinkan"):
        download_clip(url, tmp_path / "a.mp4", HOSTS, 10_000_000, serving(mp4_bytes))


def test_download_refuses_big_broken_and_rejected_files_and_leaves_nothing(tmp_path, mp4_bytes):
    url = "https://cdn.example/a.mp4"
    with pytest.raises(FootageError, match="terlalu besar"):
        download_clip(url, tmp_path / "a.mp4", HOSTS, 100, serving(mp4_bytes))
    with pytest.raises(FootageError, match="tidak bisa diputar"):
        download_clip(url, tmp_path / "a.mp4", HOSTS, 10_000_000, serving(b"not a video" * 50))
    with pytest.raises(FootageError, match="HTTP 403"):
        download_clip(url, tmp_path / "a.mp4", HOSTS, 10_000_000, serving(b"", 403))
    assert list(tmp_path.iterdir()) == []


def words(*spans):
    return [{"word": w, "start": s, "end": e} for w, s, e in spans]


def test_shot_durations_cover_the_whole_narration():
    heard = words(("Hook", 0.2, 0.8), ("body", 1.0, 2.0), ("end", 2.5, 3.0))

    durations = shot_durations(["Hook", "body", "end"], heard)

    assert durations == pytest.approx([1.0, 1.5, 0.8])  # 0-1.0, 1.0-2.5, 2.5-3.3


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_job(db, segments=2):
    db.add(Topic(id="t", text="Jupiter's Great Red Spot", category="space"))
    job = Job(id="job_f", topic_id="t", status=JobState.SELECTING_FOOTAGE.value)
    db.add(job)
    texts = [f"Segment {i} text here." for i in range(segments)]
    db.add(
        Script(
            id="s",
            job_id="job_f",
            hook="Jupiter spins.",
            segments=[{"text": t, "visual_suggestion": "jupiter storm"} for t in texts],
            ending="The end.",
            structure="x",
            status="passed",
        )
    )
    audio = get_data_dir() / "jobs" / "job_f" / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    spoken = ["Jupiter", "spins.", *[w for t in texts for w in t.split()], "The", "end."]
    heard = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(spoken)]
    (audio / "alignment.json").write_text(json.dumps(heard))
    db.commit()
    return job


def test_stage_downloads_one_clip_per_segment_and_writes_the_plan(db_session, mp4_bytes):
    job = make_job(db_session)
    provider = FakeProvider(
        {"jupiter storm": [cand("a", ["jupiter"], 6), cand("b", ["jupiter", "storm"], 6)]}
    )
    config = get_config()
    config.settings["footage"] = {**config.settings["footage"], "allowed_download_hosts": HOSTS}

    assert run_footage_selection(db_session, job, provider, config, serving(mp4_bytes)) is True

    plan_file = get_data_dir() / "jobs" / "job_f" / "footage" / "footage_plan.json"
    plan = json.loads(plan_file.read_text())
    assert [p["asset_id"] for p in plan] == ["b", "a"] and plan[0]["attribution"] == "Video b"
    assert all(p["loop"] for p in plan)  # the 2 s test clips are shorter than the shots
    assert job.status == JobState.RENDERING.value


def test_stage_drops_the_job_when_a_segment_has_no_clip(db_session, mp4_bytes):
    job = make_job(db_session)

    assert (
        run_footage_selection(db_session, job, FakeProvider({}), get_config(), serving(mp4_bytes))
        is False
    )
    assert job.status == JobState.DROPPED.value


def test_a_clip_outside_the_channels_field_is_never_picked_even_if_words_match():
    tornado = cand("tornado", ["storm", "clouds", "tornado"])
    ok = cand("ok", ["jupiter", "space"])
    provider = FakeProvider({"jupiter storm": [tornado, ok]})
    settings = {"required_tags_any": ["space"], "min_clip_seconds": 5}

    [pick] = pick_footage(provider, [Shot(0, "jupiter storm clouds", 8.0)], "Jupiter", settings)

    assert pick.candidate.asset_id == "ok"
    assert relevance(tornado, {"storm"}, set(), {"space"}) == -1
