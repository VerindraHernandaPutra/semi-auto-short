from enum import Enum

from sqlalchemy.orm import Session

from shorts_engine.db.models import Job, JobEvent


class JobState(str, Enum):
    TOPIC_QUEUED = "topic_queued"
    RESEARCHING = "researching"
    VERIFYING = "verifying"
    SCRIPTING = "scripting"
    SCRIPT_CHECK = "script_check"
    AWAITING_VOICE = "awaiting_voice"
    PROCESSING_AUDIO = "processing_audio"
    ALIGNING = "aligning"
    SELECTING_FOOTAGE = "selecting_footage"
    RENDERING = "rendering"
    QUALITY_CHECK = "quality_check"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EXPORTED = "exported"
    PUBLISHED = "published"

    # Status alternatif
    DROPPED = "dropped"
    CHANGES_REQUESTED = "changes_requested"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidStateTransition(ValueError):
    """Raised when a job is moved along an unsupported state transition."""


ALLOWED_TRANSITIONS = {
    JobState.TOPIC_QUEUED: {
        JobState.RESEARCHING,
        JobState.DROPPED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.RESEARCHING: {
        JobState.VERIFYING,
        JobState.DROPPED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.VERIFYING: {
        JobState.SCRIPTING,
        JobState.DROPPED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.SCRIPTING: {
        JobState.SCRIPT_CHECK,
        JobState.AWAITING_VOICE,
        JobState.DROPPED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.SCRIPT_CHECK: {
        JobState.SCRIPTING,
        JobState.AWAITING_VOICE,
        JobState.DROPPED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.AWAITING_VOICE: {
        JobState.PROCESSING_AUDIO,
        JobState.ALIGNING,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.PROCESSING_AUDIO: {
        JobState.ALIGNING,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.ALIGNING: {
        JobState.SELECTING_FOOTAGE,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.SELECTING_FOOTAGE: {
        JobState.RENDERING,
        JobState.DROPPED,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.RENDERING: {
        JobState.QUALITY_CHECK,
        JobState.PENDING_REVIEW,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.QUALITY_CHECK: {
        JobState.RENDERING,
        JobState.PENDING_REVIEW,
        JobState.FAILED,
        JobState.CANCELLED,
    },
    JobState.PENDING_REVIEW: {
        JobState.APPROVED,
        JobState.CHANGES_REQUESTED,
        JobState.CANCELLED,
    },
    JobState.CHANGES_REQUESTED: {
        JobState.SCRIPTING,
        JobState.AWAITING_VOICE,
        JobState.CANCELLED,
    },
    JobState.APPROVED: {JobState.EXPORTED},
    JobState.EXPORTED: {JobState.PUBLISHED},
    JobState.PUBLISHED: set(),
    JobState.DROPPED: set(),
    JobState.FAILED: set(),
    JobState.CANCELLED: set(),
}


def transition_job_state(
    db: Session,
    job: Job,
    new_state: JobState,
    actor: str = "system",
    reason: str | None = None,
):
    old_state = job.status

    try:
        current_state = JobState(old_state)
    except ValueError as exc:
        raise InvalidStateTransition(
            f"Job {job.id} has unknown current state: {old_state!r}"
        ) from exc

    if new_state not in ALLOWED_TRANSITIONS[current_state]:
        raise InvalidStateTransition(
            f"Job {job.id} cannot transition from {current_state.value!r} to {new_state.value!r}"
        )

    job.status = new_state.value

    if new_state == JobState.FAILED:
        job.last_valid_state = old_state
        job.error = reason

    event = JobEvent(
        job_id=job.id,
        from_state=old_state,
        to_state=new_state.value,
        actor=actor,
        reason=reason,
    )

    db.add(event)
    db.commit()
    db.refresh(job)
    return job
