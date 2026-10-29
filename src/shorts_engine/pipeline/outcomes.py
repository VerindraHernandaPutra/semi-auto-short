from sqlalchemy.orm import Session

from shorts_engine.db.models import Job
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.providers.llm.base import LLMOutputError, LLMProviderError


def end_on_llm_error(db: Session, job: Job, exc: LLMProviderError, what: str) -> None:
    """Drop the topic when the model answered off-schema; fail the job when the service broke.

    An off-schema answer is about this topic's content, so the next topic may well work. A broken
    key or network fails every topic, so the run must stop (a FAILED job stops ``produce``).
    """
    if isinstance(exc, LLMOutputError):
        transition_job_state(db, job, JobState.DROPPED, reason=f"llm_output_invalid:{what}: {exc}")
    else:
        transition_job_state(db, job, JobState.FAILED, reason=f"{what} failed: {exc}")
