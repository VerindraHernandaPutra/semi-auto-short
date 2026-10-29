import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, Job, Script, Source, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.description import (
    build_description,
    build_title,
    cited_claim_ids,
    write_description,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_job(db, title="Jupiter's Great Red Spot Has Been Shrinking", episode=1):
    db.add(Topic(id="t", text="x", category="space"))
    job = Job(id="job_d", topic_id="t", status=JobState.RENDERING.value)
    db.add(job)
    for sid, url, name in (
        ("s1", "https://nasa.example/a", "NASA page"),
        ("s2", "https://yale.example/b", None),
        ("s3", "https://unused.example/c", "Unused"),
    ):
        db.add(
            Source(
                id=sid, job_id="job_d", url=url, domain=url.split("/")[2], title=name, tier="tier_a"
            )
        )
    for cid, sources in (("c1", ["s1"]), ("c2", ["s1", "s2"]), ("c3", ["s3"])):
        db.add(Claim(id=cid, job_id="job_d", source_ids=sources, text=cid, supporting_quote=cid,
                     quote_match_score=1.0, support_check="supports", status="verified"))  # fmt: skip
    db.add(
        Script(
            id="sc", job_id="job_d", hook="Jupiter [JOO-pih-ter] is shrinking.",
            segments=[{"text": "It was seen in 1831.", "claim_ids": ["c2"]},
                      {"text": "More text.", "claim_ids": ["c1"]}],
            ending="Gravity data.", structure="x", status="passed",
            check_results={"hook_claim_ids": ["c1"], "ending_claim_ids": [],
                           "title_working": title, "series": "Everyone Gets This Wrong",
                           "episode_number": episode},
        )
    )  # fmt: skip
    db.commit()
    return job, db.query(Script).one()


def test_only_cited_claims_count_in_order_of_first_use(db_session):
    _, script = make_job(db_session)

    assert cited_claim_ids(script) == ["c1", "c2"]


def test_title_gets_the_series_label_only_when_it_fits(db_session):
    _, script = make_job(db_session)
    assert (
        build_title(script) == "Jupiter's Great Red Spot Has Been Shrinking"
    )  # label would pass 60

    short = script
    short.check_results = {**script.check_results, "title_working": "Jupiter shrinks"}
    assert build_title(short) == "Jupiter shrinks Everyone Gets This Wrong #1"


def test_description_lists_only_sources_of_cited_claims_with_database_urls(db_session, tmp_path):
    job, _ = make_job(db_session)
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / "footage_plan.json").write_text(
        json.dumps(
            [{"attribution": "Video by A from Pixabay"}, {"attribution": "Video by A from Pixabay"}]
        )
    )

    text = build_description(db_session, job, tmp_path, get_config())

    assert text.startswith("Jupiter is shrinking. It was seen in 1831.")  # hint removed
    assert "- NASA page: https://nasa.example/a" in text
    assert "- yale.example: https://yale.example/b" in text  # no title: domain used
    assert "unused.example" not in text
    assert text.count("Video by A from Pixabay") == 1
    assert "illustrative stock footage, not mission imagery" in text
    assert text.strip().endswith("#Shorts #Space #Astronomy #Science")


def test_description_file_is_written_next_to_the_video(db_session, tmp_path):
    job, _ = make_job(db_session)

    path = write_description(db_session, job, tmp_path, get_config())

    assert path == tmp_path / "render" / "description.txt"
    assert path.read_text(encoding="utf-8").startswith("Title: ")
