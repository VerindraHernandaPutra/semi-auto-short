import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.base import Base
from shorts_engine.db.models import Job, JobEvent, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.outcomes import end_on_llm_error
from shorts_engine.providers.llm.base import LLMOutputError, LLMProviderError


@pytest.fixture
def job_and_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Topic(id="t", text="x", category="space"))
    job = Job(id="job_o", topic_id="t", status=JobState.VERIFYING.value)
    db.add(job)
    db.commit()
    yield db, job
    db.close()


def last_reason(db):
    return db.query(JobEvent).order_by(JobEvent.id.desc()).first().reason


def test_an_off_schema_answer_drops_the_topic_so_the_run_goes_on(job_and_db):
    db, job = job_and_db

    end_on_llm_error(
        db,
        job,
        LLMOutputError("anthropic", "output did not match X (a: missing)"),
        "claim extraction",
    )

    assert job.status == JobState.DROPPED.value
    assert last_reason(db).startswith("llm_output_invalid:claim extraction:")


def test_a_broken_service_fails_the_job_so_the_run_stops(job_and_db):
    db, job = job_and_db

    end_on_llm_error(
        db, job, LLMProviderError("anthropic", "request failed (HTTP 401)"), "claim extraction"
    )

    assert job.status == JobState.FAILED.value
    assert "HTTP 401" in last_reason(db)
