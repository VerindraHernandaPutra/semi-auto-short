from sqlalchemy.orm import Session

from shorts_engine.db.models import Job, Topic
from shorts_engine.domain.state_machine import JobState


def release_unstarted_job(db: Session, topic: Topic, job: Job, queued_status: str) -> bool:
    """Undo a job that a budget or quota check refused before any work happened.

    The topic returns to the queue and the empty job is removed. A job that already did work
    (research or later) is kept so its paid results are not lost. Returns True if released.
    """
    if job.status != JobState.TOPIC_QUEUED.value:
        return False
    topic.status = queued_status
    db.delete(job)
    db.commit()
    return True
