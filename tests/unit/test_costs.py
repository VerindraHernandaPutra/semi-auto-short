from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import CostEntry, Topic
from shorts_engine.domain.schemas import ClaimSupportCheck
from shorts_engine.providers.llm.anthropic import AnthropicProvider
from shorts_engine.providers.llm.base import LLMProviderError, LLMUsage
from shorts_engine.services.costs import (
    BudgetExceeded,
    budget_status,
    check_budget,
    cost_report,
    make_usage_listener,
    record_llm_usage,
    usd_cost,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
HAIKU = "claude-haiku-5-5"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def add_cost(db, usd, when=NOW, job_id=None, operation="TopicList"):
    db.add(
        CostEntry(
            job_id=job_id,
            provider="anthropic",
            model=HAIKU,
            operation=operation,
            input_units=10,
            output_units=5,
            cost_usd=usd,
            created_at=when,
        )
    )
    db.commit()


def test_cost_comes_from_configured_prices_not_hardcoded_values():
    config = get_config()

    # 1M input at $0.10 plus 1M output at $0.50
    assert usd_cost(config, HAIKU, 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert usd_cost(config, "unknown-model", 100, 100) is None


def test_recorded_usage_is_committed_with_a_cost(db_session):
    usage = LLMUsage("anthropic", HAIKU, "TopicList", 2000, 1000)

    entry = record_llm_usage(db_session, get_config(), usage, job_id="job_1")

    stored = db_session.query(CostEntry).one()
    assert stored.id == entry.id
    assert (stored.input_units, stored.output_units, stored.cost_kind) == (2000, 1000, "actual")
    assert stored.cost_usd == pytest.approx(0.0007)
    assert stored.job_id == "job_1"


def test_unpriced_models_are_flagged_instead_of_costed_at_zero(db_session):
    usage = LLMUsage("anthropic", "future-model", "TopicList", 10, 10)

    record_llm_usage(db_session, get_config(), usage)

    stored = db_session.query(CostEntry).one()
    assert stored.cost_usd is None and stored.cost_kind == "unpriced"


def test_monthly_budget_blocks_paid_stages_and_ignores_other_months(db_session):
    config = get_config()  # budget Rp100.000, rate Rp15.500 per USD
    add_cost(db_session, 5.0, when=datetime(2026, 9, 20, tzinfo=UTC))  # last month: ignored
    assert check_budget(db_session, config, now=NOW).month_idr == 0

    add_cost(db_session, 7.0)  # about Rp108.500

    with pytest.raises(BudgetExceeded) as raised:
        check_budget(db_session, config, now=NOW)
    assert raised.value.scope == "monthly"


def test_warning_starts_at_eighty_percent_of_the_monthly_budget(db_session):
    config = get_config()
    add_cost(db_session, 4.0)  # Rp62.000 = 62% of Rp100.000
    assert budget_status(db_session, config, now=NOW).warning is False

    add_cost(db_session, 1.5)  # total Rp85.250 = 85%: warn, but still allowed
    status = check_budget(db_session, config, now=NOW)
    assert status.warning is True


def test_per_video_budget_counts_only_that_job(db_session):
    config = get_config()  # per video Rp5.000
    add_cost(db_session, 0.5, job_id="job_a")  # Rp7.750 > 5.000
    add_cost(db_session, 0.01, job_id="job_b")

    with pytest.raises(BudgetExceeded) as raised:
        check_budget(db_session, config, job_id="job_a", now=NOW)
    assert raised.value.scope == "per-video"
    assert check_budget(db_session, config, job_id="job_b", now=NOW).job_idr < 5000


def test_topic_queue_spending_counts_toward_the_month_but_has_no_job_limit(db_session):
    add_cost(db_session, 0.5)  # no job_id

    status = check_budget(db_session, get_config(), now=NOW)

    assert status.month_idr == pytest.approx(7750)
    assert status.job_idr is None


class FakeMessages:
    def __init__(self, response):
        self.response = response

    def create(self, **kwargs):
        return self.response


def provider_with(response):
    return AnthropicProvider(api_key="k", client=SimpleNamespace(messages=FakeMessages(response)))


def response(stop_reason="end_turn", parsed="ok"):
    text = parsed.model_dump_json() if hasattr(parsed, "model_dump_json") else None
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)] if text else [],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=321, output_tokens=45),
    )


def test_provider_reports_usage_to_the_listener():
    seen = []
    provider = provider_with(response(parsed=ClaimSupportCheck(status="no", reason="x")))
    provider.usage_listener = seen.append

    provider.generate_structured("p", ClaimSupportCheck, HAIKU)

    assert seen == [LLMUsage("anthropic", HAIKU, "ClaimSupportCheck", 321, 45)]


def test_usage_is_reported_even_when_the_call_ends_in_a_refusal():
    seen = []
    provider = provider_with(response(stop_reason="refusal", parsed=None))
    provider.usage_listener = seen.append

    with pytest.raises(LLMProviderError):
        provider.generate_structured("p", ClaimSupportCheck, HAIKU)

    assert len(seen) == 1  # the refused call was still billed


def test_listener_factory_writes_cost_entries(db_session):
    listener = make_usage_listener(db_session, get_config(), job_id="job_9")

    listener(LLMUsage("anthropic", HAIKU, "ScriptOutput", 100, 50))

    assert db_session.query(CostEntry).one().job_id == "job_9"


def test_cost_report_groups_operations_and_counts_topics(db_session):
    add_cost(db_session, 0.01, operation="TopicList")
    add_cost(db_session, 0.03, operation="TopicList")
    add_cost(db_session, 0.02, operation="TopicPolicyCheck")
    for status, count in (("queued", 2), ("used", 1), ("rejected", 3)):
        for i in range(count):
            db_session.add(Topic(id=f"{status}{i}", text=status, category="animals", status=status))
    db_session.commit()

    report = cost_report(db_session, get_config(), now=NOW)

    by_name = {item.operation: item for item in report.operations}
    assert by_name["TopicList"].calls == 2
    assert by_name["TopicList"].cost_idr == pytest.approx(0.04 * 15500)
    assert report.operations[0].operation == "TopicList"  # most expensive first
    assert (report.topics_queued, report.topics_used, report.topics_rejected) == (2, 1, 3)
