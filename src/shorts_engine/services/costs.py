import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig
from shorts_engine.db.models import CostEntry, Topic
from shorts_engine.providers.llm.base import LLMUsage

logger = logging.getLogger(__name__)

WARNING_RATIO = 0.8  # SPEC 14: warn at 80% of the monthly budget


class BudgetExceeded(RuntimeError):
    """A paid stage was refused because a budget limit has been reached."""

    def __init__(self, scope: str, spent_idr: float, limit_idr: float):
        super().__init__(f"{scope} budget reached: Rp{spent_idr:,.0f} of Rp{limit_idr:,.0f}")
        self.scope = scope
        self.spent_idr = spent_idr
        self.limit_idr = limit_idr


@dataclass
class BudgetStatus:
    month_idr: float
    month_limit_idr: float
    job_idr: float | None
    job_limit_idr: float
    warning: bool


@dataclass
class OperationCost:
    operation: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_idr: float


@dataclass
class CostReport:
    status: BudgetStatus
    operations: list[OperationCost] = field(default_factory=list)
    unpriced_entries: int = 0
    topics_queued: int = 0
    topics_used: int = 0
    topics_rejected: int = 0


def usd_cost(config: AppConfig, model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Cost in USD from configured per-million-token prices; None when the model has no price."""
    price = config.settings.get("pricing", {}).get(model)
    if not price:
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def record_llm_usage(
    db: Session, config: AppConfig, usage: LLMUsage, job_id: str | None = None
) -> CostEntry:
    """Store one LLM call and commit immediately.

    The commit is deliberate: money was spent even if the surrounding stage later rolls back.
    Call this while the session holds no other uncommitted business changes.
    """
    cost = usd_cost(config, usage.model, usage.input_tokens, usage.output_tokens)
    if cost is None:
        logger.warning("No price configured for model %s; cost recorded as unpriced", usage.model)
    entry = CostEntry(
        job_id=job_id,
        provider=usage.provider,
        model=usage.model,
        operation=usage.operation,
        input_units=usage.input_tokens,
        output_units=usage.output_tokens,
        cost_usd=cost,
        cost_kind="actual" if cost is not None else "unpriced",
    )
    db.add(entry)
    db.commit()
    return entry


def make_usage_listener(
    db: Session, config: AppConfig, job_id: str | None = None
) -> Callable[[LLMUsage], None]:
    return lambda usage: record_llm_usage(db, config, usage, job_id)


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = (
        datetime(now.year + 1, 1, 1, tzinfo=UTC)
        if now.month == 12
        else datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    )
    return start, end


def _sum_usd(db: Session, *conditions) -> float:
    value = db.query(func.coalesce(func.sum(CostEntry.cost_usd), 0.0)).filter(*conditions).scalar()
    return float(value or 0.0)


def budget_status(
    db: Session, config: AppConfig, job_id: str | None = None, now: datetime | None = None
) -> BudgetStatus:
    now = now or datetime.now(UTC)
    costs = config.settings["costs"]
    rate = float(costs["usd_to_idr"])
    start, end = _month_bounds(now)
    month_idr = _sum_usd(db, CostEntry.created_at >= start, CostEntry.created_at < end) * rate
    job_idr = _sum_usd(db, CostEntry.job_id == job_id) * rate if job_id else None
    month_limit = float(costs["budget_monthly_idr"])
    return BudgetStatus(
        month_idr=month_idr,
        month_limit_idr=month_limit,
        job_idr=job_idr,
        job_limit_idr=float(costs["budget_per_video_idr"]),
        warning=month_idr >= WARNING_RATIO * month_limit,
    )


def check_budget(
    db: Session, config: AppConfig, job_id: str | None = None, now: datetime | None = None
) -> BudgetStatus:
    """Raise BudgetExceeded before a paid stage when the monthly or per-video limit is reached.

    Topic-queue spending has no job and counts toward the monthly budget only.
    """
    status = budget_status(db, config, job_id, now)
    if status.month_idr >= status.month_limit_idr:
        raise BudgetExceeded("monthly", status.month_idr, status.month_limit_idr)
    if status.job_idr is not None and status.job_idr >= status.job_limit_idr:
        raise BudgetExceeded("per-video", status.job_idr, status.job_limit_idr)
    return status


def cost_report(db: Session, config: AppConfig, now: datetime | None = None) -> CostReport:
    now = now or datetime.now(UTC)
    rate = float(config.settings["costs"]["usd_to_idr"])
    start, end = _month_bounds(now)
    rows = (
        db.query(
            CostEntry.operation,
            func.count(CostEntry.id),
            func.coalesce(func.sum(CostEntry.input_units), 0),
            func.coalesce(func.sum(CostEntry.output_units), 0),
            func.coalesce(func.sum(CostEntry.cost_usd), 0.0),
        )
        .filter(CostEntry.created_at >= start, CostEntry.created_at < end)
        .group_by(CostEntry.operation)
        .order_by(func.sum(CostEntry.cost_usd).desc())
        .all()
    )
    unpriced = (
        db.query(CostEntry)
        .filter(
            CostEntry.created_at >= start, CostEntry.created_at < end, CostEntry.cost_usd.is_(None)
        )
        .count()
    )
    counts = dict(db.query(Topic.status, func.count(Topic.id)).group_by(Topic.status).all())
    return CostReport(
        status=budget_status(db, config, now=now),
        operations=[
            OperationCost(op, calls, int(tin), int(tout), float(usd) * rate)
            for op, calls, tin, tout, usd in rows
        ],
        unpriced_entries=unpriced,
        topics_queued=counts.get("queued", 0),
        topics_used=counts.get("used", 0),
        topics_rejected=counts.get("rejected", 0),
    )
