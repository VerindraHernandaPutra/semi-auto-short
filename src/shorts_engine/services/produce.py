import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import desc
from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.models import Job, JobEvent, Topic
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.research import run_research
from shorts_engine.pipeline.script import run_script
from shorts_engine.pipeline.topics import (
    STATUS_QUEUED,
    STATUS_REJECTED,
    TopicGenerationError,
    refill_topic_queue,
)
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.providers.factory import (
    get_footage_provider,
    get_llm_provider,
    get_search_provider,
)
from shorts_engine.providers.footage.base import BaseFootageProvider
from shorts_engine.providers.llm.base import BaseLLMProvider
from shorts_engine.providers.search.base import BaseSearchProvider
from shorts_engine.services.costs import (
    BudgetExceeded,
    SearchQuotaExceeded,
    budget_status,
)
from shorts_engine.services.jobs import release_unstarted_job

logger = logging.getLogger(__name__)

STATUS_USED = "used"

# Why a run stopped before reaching the requested number of scripts.
STOP_BUDGET = "budget"
STOP_QUOTA = "quota"
STOP_QUEUE = "queue_exhausted"
STOP_ATTEMPTS = "max_attempts"
STOP_FAILED = "job_failed"
STOP_REFILL = "refill_failed"


@dataclass
class Attempt:
    topic: str
    job_id: str | None
    outcome: str  # "ready", "dropped", "failed", "interrupted"
    detail: str = ""


@dataclass
class ProduceReport:
    requested: int
    ready_job_ids: list[str] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    stopped: str | None = None
    stop_detail: str = ""
    cost_idr: float = 0.0


def _last_reason(db: Session, job: Job) -> str:
    event = (
        db.query(JobEvent).filter(JobEvent.job_id == job.id).order_by(JobEvent.id.desc()).first()
    )
    return (event.reason if event and event.reason else job.error) or ""


def _next_topic(db: Session) -> Topic | None:
    return db.query(Topic).filter(Topic.status == STATUS_QUEUED).order_by(desc(Topic.score)).first()


def _run_stages(db, job, llm, search, config) -> None:
    """Research, verify and script; each stage moves the job to a terminal state on failure."""
    if run_research(db, job, llm=llm, search=search, config=config) and run_verify(
        db, job, llm=llm, search=search, config=config
    ):
        run_script(db, job, llm=llm, config=config)


def produce_scripts(
    db: Session,
    count: int,
    llm: BaseLLMProvider | None = None,
    search: BaseSearchProvider | None = None,
    footage: BaseFootageProvider | None = None,
    config: AppConfig | None = None,
) -> ProduceReport:
    """Produce ``count`` recordable scripts, replacing every dropped topic with the next one.

    Dropping a topic is normal and never asks anything of the user (SPEC 7.3). The run is bounded
    by ``produce.max_attempts_per_script`` attempts per requested script, stops at the first
    failed job (which usually means a broken key or service) and at any budget or quota limit.
    """
    config = config or get_config()
    llm = llm or get_llm_provider()
    search = search or get_search_provider()
    footage = footage or get_footage_provider()
    max_attempts = count * int(config.settings.get("produce", {}).get("max_attempts_per_script", 3))

    report = ProduceReport(requested=count)
    spent_before = budget_status(db, config).month_idr

    try:
        while len(report.ready_job_ids) < count:
            if len(report.attempts) >= max_attempts:
                report.stopped = STOP_ATTEMPTS
                break

            topic = _next_topic(db)
            if topic is None:
                try:
                    refill_topic_queue(db, llm=llm, footage=footage, config=config)
                except TopicGenerationError as exc:
                    report.stopped, report.stop_detail = STOP_REFILL, str(exc)
                    break
                topic = _next_topic(db)
                if topic is None:
                    report.stopped = STOP_QUEUE
                    break

            topic.status = STATUS_USED
            job = Job(
                id=f"job_{uuid.uuid4().hex[:8]}",
                topic_id=topic.id,
                status=JobState.TOPIC_QUEUED.value,
            )
            db.add(job)
            db.commit()
            topic_text = topic.text

            try:
                _run_stages(db, job, llm, search, config)
            except (BudgetExceeded, SearchQuotaExceeded) as exc:
                report.stopped = STOP_BUDGET if isinstance(exc, BudgetExceeded) else STOP_QUOTA
                report.stop_detail = str(exc)
                if release_unstarted_job(db, topic, job, STATUS_QUEUED):
                    report.attempts.append(
                        Attempt(topic_text, None, "interrupted", "returned to queue")
                    )
                else:  # paid work already exists: keep the job, it can be resumed
                    report.attempts.append(
                        Attempt(topic_text, job.id, "interrupted", f"kept at {job.status}")
                    )
                break

            if job.status == JobState.AWAITING_VOICE.value:
                report.ready_job_ids.append(job.id)
                report.attempts.append(Attempt(topic_text, job.id, "ready"))
            elif job.status == JobState.DROPPED.value:
                reason = _last_reason(db, job)
                topic.status, topic.reason = STATUS_REJECTED, f"dropped:{reason}"[:300]
                db.commit()
                report.attempts.append(Attempt(topic_text, job.id, "dropped", reason))
            else:
                # FAILED (or anything unexpected): usually a broken key or service, so stop
                # instead of burning the queue. The topic stays attached to its resumable job.
                reason = _last_reason(db, job)
                topic.reason = f"job_failed:{job.id}"
                db.commit()
                report.attempts.append(Attempt(topic_text, job.id, "failed", reason))
                report.stopped, report.stop_detail = STOP_FAILED, reason
                break
    finally:
        report.cost_idr = max(0.0, budget_status(db, config).month_idr - spent_before)
    return report
