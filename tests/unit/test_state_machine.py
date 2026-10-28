import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.base import Base
from shorts_engine.db.models import Job, JobEvent, Topic
from shorts_engine.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransition,
    JobState,
    transition_job_state,
)


@pytest.fixture
def db_session():
    # Menggunakan SQLite in-memory khusus untuk isolasi testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_state_transition(db_session):
    topic = Topic(id="top_1", text="Test Topic", category="animals", status="queued")
    db_session.add(topic)
    db_session.commit()

    job = Job(id="job_1", topic_id="top_1", status=JobState.TOPIC_QUEUED.value)
    db_session.add(job)
    db_session.commit()

    updated_job = transition_job_state(
        db=db_session,
        job=job,
        new_state=JobState.RESEARCHING,
        actor="system",
        reason="Mulai riset",
    )

    assert updated_job.status == JobState.RESEARCHING.value
    event = db_session.query(JobEvent).one()
    assert event.from_state == JobState.TOPIC_QUEUED.value
    assert event.to_state == JobState.RESEARCHING.value


def test_transition_map_covers_every_state():
    assert set(ALLOWED_TRANSITIONS) == set(JobState)
    assert all(
        isinstance(next_state, JobState)
        for transitions in ALLOWED_TRANSITIONS.values()
        for next_state in transitions
    )


@pytest.mark.parametrize(
    ("current_state", "new_state"),
    [
        (JobState.RESEARCHING, JobState.VERIFYING),
        (JobState.VERIFYING, JobState.SCRIPTING),
        (JobState.AWAITING_VOICE, JobState.ALIGNING),
        (JobState.ALIGNING, JobState.SELECTING_FOOTAGE),
        (JobState.SELECTING_FOOTAGE, JobState.RENDERING),
        (JobState.RENDERING, JobState.PENDING_REVIEW),
        (JobState.PENDING_REVIEW, JobState.APPROVED),
        (JobState.APPROVED, JobState.EXPORTED),
        (JobState.EXPORTED, JobState.PUBLISHED),
    ],
)
def test_valid_state_transitions(db_session, current_state, new_state):
    topic = Topic(id="top_1", text="Test Topic", category="animals", status="queued")
    db_session.add(topic)
    job = Job(id="job_1", topic_id=topic.id, status=current_state.value)
    db_session.add(job)
    db_session.commit()

    transition_job_state(db_session, job, new_state)

    assert job.status == new_state.value
    assert db_session.query(JobEvent).one().to_state == new_state.value


@pytest.mark.parametrize(
    ("current_state", "new_state"),
    [
        (JobState.TOPIC_QUEUED, JobState.APPROVED),
        (JobState.RESEARCHING, JobState.AWAITING_VOICE),
        (JobState.PENDING_REVIEW, JobState.PUBLISHED),
        (JobState.PUBLISHED, JobState.APPROVED),
        (JobState.FAILED, JobState.RESEARCHING),
    ],
)
def test_invalid_state_transitions_are_rejected_without_mutation(
    db_session, current_state, new_state
):
    topic = Topic(id="top_1", text="Test Topic", category="animals", status="queued")
    db_session.add(topic)
    job = Job(id="job_1", topic_id=topic.id, status=current_state.value)
    db_session.add(job)
    db_session.commit()

    with pytest.raises(InvalidStateTransition):
        transition_job_state(db_session, job, new_state)

    assert job.status == current_state.value
    assert db_session.query(JobEvent).count() == 0


def test_failure_transition_records_last_valid_state_and_reason(db_session):
    topic = Topic(id="top_1", text="Test Topic", category="animals", status="queued")
    db_session.add(topic)
    job = Job(id="job_1", topic_id=topic.id, status=JobState.RESEARCHING.value)
    db_session.add(job)
    db_session.commit()

    transition_job_state(db_session, job, JobState.FAILED, reason="Search failed")

    assert job.status == JobState.FAILED.value
    assert job.last_valid_state == JobState.RESEARCHING.value
    assert job.error == "Search failed"


def test_unknown_current_state_is_rejected(db_session):
    topic = Topic(id="top_1", text="Test Topic", category="animals", status="queued")
    db_session.add(topic)
    job = Job(id="job_1", topic_id=topic.id, status="not_a_state")
    db_session.add(job)
    db_session.commit()

    with pytest.raises(InvalidStateTransition, match="unknown current state"):
        transition_job_state(db_session, job, JobState.RESEARCHING)

    assert job.status == "not_a_state"
    assert db_session.query(JobEvent).count() == 0
