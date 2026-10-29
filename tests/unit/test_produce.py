import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from shorts_engine.cli import app
from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import CostEntry, Job, Topic
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.script_rules import find_unclaimed_overstatements
from shorts_engine.pipeline.topics import RefillReport, TopicGenerationError
from shorts_engine.services import produce as produce_module
from shorts_engine.services.costs import BudgetExceeded, SearchQuotaExceeded
from shorts_engine.services.produce import produce_scripts

runner = CliRunner()


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def add_topic(db, text, score=5.0, status="queued"):
    topic = Topic(id=f"t_{text}", text=text, category="animals", score=score, status=status)
    db.add(topic)
    db.commit()
    return topic


def topic_of(db, job):
    return db.query(Topic).filter(Topic.id == job.topic_id).one()


class Stages:
    """Scenario stages: what happens to a job depends on its topic text, like a real run."""

    def __init__(self, db):
        self.db = db
        self.order: list[str] = []

    def research(self, db, job, **_):
        text = topic_of(db, job).text
        self.order.append(text)
        if text.startswith("budget-early"):
            raise BudgetExceeded("monthly", 100.0, 100.0)
        if text.startswith("quota"):
            raise SearchQuotaExceeded("tavily", 899, 900)
        transition_job_state(db, job, JobState.RESEARCHING, reason="go")
        if text.startswith("drop"):
            transition_job_state(db, job, JobState.DROPPED, reason="insufficient_sources:1_domains")
            return False
        if text.startswith("fail"):
            transition_job_state(db, job, JobState.FAILED, reason="Pencarian gagal: auth")
            return False
        transition_job_state(db, job, JobState.VERIFYING, reason="ok")
        return True

    def verify(self, db, job, **_):
        if topic_of(db, job).text.startswith("budget-late"):
            raise BudgetExceeded("per-video", 6000.0, 5000.0)
        return True

    def script(self, db, job, **_):
        transition_job_state(db, job, JobState.SCRIPTING, reason="go")
        db.add(
            CostEntry(provider="anthropic", operation="ScriptOutput", job_id=job.id, cost_usd=0.1)
        )
        db.commit()
        transition_job_state(db, job, JobState.AWAITING_VOICE, reason="ready")
        return True


@pytest.fixture
def stages(db_session, monkeypatch):
    scenario = Stages(db_session)
    monkeypatch.setattr(produce_module, "run_research", scenario.research)
    monkeypatch.setattr(produce_module, "run_verify", scenario.verify)
    monkeypatch.setattr(produce_module, "run_script", scenario.script)
    return scenario


def produce(db, count, **kwargs):
    return produce_scripts(
        db, count, llm=object(), search=object(), footage=object(), config=get_config(), **kwargs
    )


def test_produces_the_requested_number_of_scripts(db_session, stages):
    for name in "abc":
        add_topic(db_session, f"ok-{name}")

    report = produce(db_session, 2)

    assert len(report.ready_job_ids) == 2 and report.stopped is None
    jobs = db_session.query(Job).all()
    assert {j.status for j in jobs} == {JobState.AWAITING_VOICE.value}
    used = db_session.query(Topic).filter_by(status="used").count()
    assert used == 2 and db_session.query(Topic).filter_by(status="queued").count() == 1


def test_highest_scoring_topics_are_produced_first(db_session, stages):
    add_topic(db_session, "ok-low", score=3.0)
    add_topic(db_session, "ok-high", score=9.0)

    produce(db_session, 1)

    assert stages.order == ["ok-high"]


def test_a_dropped_topic_is_replaced_by_the_next_one(db_session, stages):
    add_topic(db_session, "drop-first", score=9.0)
    add_topic(db_session, "ok-second", score=5.0)

    report = produce(db_session, 1)

    assert len(report.ready_job_ids) == 1 and report.stopped is None
    assert [a.outcome for a in report.attempts] == ["dropped", "ready"]
    dropped = db_session.query(Topic).filter_by(text="drop-first").one()
    assert dropped.status == "rejected"  # not "used": the dropped-versus-used ratio stays true
    assert dropped.reason == "dropped:insufficient_sources:1_domains"
    assert db_session.query(Topic).filter_by(text="ok-second").one().status == "used"


def test_the_run_stops_after_the_attempt_limit_instead_of_burning_the_queue(db_session, stages):
    for number in range(10):
        add_topic(db_session, f"drop-{number}")

    report = produce(db_session, 1)

    assert report.stopped == "max_attempts"
    assert len(report.attempts) == 3  # produce.max_attempts_per_script
    assert db_session.query(Topic).filter_by(status="queued").count() == 7


def test_an_empty_queue_is_refilled_automatically(db_session, stages, monkeypatch):
    calls = []

    def fake_refill(db, **kwargs):
        calls.append(kwargs)
        add_topic(db, "ok-refilled")
        return RefillReport()

    monkeypatch.setattr(produce_module, "refill_topic_queue", fake_refill)

    report = produce(db_session, 1)

    assert len(calls) == 1 and len(report.ready_job_ids) == 1


def test_a_refill_that_finds_nothing_ends_the_run_cleanly(db_session, stages, monkeypatch):
    monkeypatch.setattr(produce_module, "refill_topic_queue", lambda db, **kw: RefillReport())

    report = produce(db_session, 1)

    assert report.stopped == "queue_exhausted" and report.attempts == []


def test_a_refill_error_is_reported_not_raised(db_session, stages, monkeypatch):
    def broken(db, **kwargs):
        raise TopicGenerationError("boom")

    monkeypatch.setattr(produce_module, "refill_topic_queue", broken)

    report = produce(db_session, 1)

    assert report.stopped == "refill_failed" and "boom" in report.stop_detail


def test_a_failed_job_stops_the_run_and_keeps_the_topic_with_its_job(db_session, stages):
    add_topic(db_session, "fail-first", score=9.0)
    add_topic(db_session, "ok-second", score=5.0)

    report = produce(db_session, 2)

    assert report.stopped == "job_failed" and "auth" in report.stop_detail
    failed_topic = db_session.query(Topic).filter_by(text="fail-first").one()
    assert failed_topic.status == "used" and failed_topic.reason.startswith("job_failed:job_")
    assert db_session.query(Topic).filter_by(text="ok-second").one().status == "queued"


def test_budget_refusal_before_any_work_returns_the_topic_to_the_queue(db_session, stages):
    add_topic(db_session, "budget-early")

    report = produce(db_session, 1)

    assert report.stopped == "budget"
    assert db_session.query(Topic).one().status == "queued"
    assert db_session.query(Job).count() == 0  # the empty job was removed
    assert report.attempts[0].outcome == "interrupted"


def test_search_quota_stops_the_run_with_its_own_reason(db_session, stages):
    add_topic(db_session, "quota-hit")

    assert produce(db_session, 1).stopped == "quota"
    assert db_session.query(Topic).one().status == "queued"


def test_budget_refusal_after_paid_work_keeps_the_job_so_it_can_resume(db_session, stages):
    add_topic(db_session, "budget-late")

    report = produce(db_session, 1)

    assert report.stopped == "budget"
    job = db_session.query(Job).one()
    assert job.status == JobState.VERIFYING.value  # research was paid for, so it is kept
    assert db_session.query(Topic).one().status == "used"


def test_the_report_states_what_the_session_cost(db_session, stages):
    add_topic(db_session, "ok-a")

    report = produce(db_session, 1)

    assert report.cost_idr == pytest.approx(0.1 * 15500)


# ------------------------------------------------------------------ the wiring


def test_cli_dry_run_never_opens_the_real_database_and_explains_the_stop(monkeypatch):
    import shorts_engine.cli as cli_module

    def real_database_must_not_be_opened():
        raise AssertionError("dry-run opened the real database")

    monkeypatch.setattr(cli_module, "SessionLocal", real_database_must_not_be_opened)

    result = runner.invoke(app, ["produce", "--count", "1", "--dry-run"])

    # Fake providers cannot verify facts, so nothing is produced; the run still ends cleanly.
    assert "--dry-run" in result.output
    assert "Naskah siap rekam: 0 dari 1" in result.output
    assert "Biaya sesi ini: Rp0" in result.output
    assert result.exit_code == 1


def test_cli_reports_a_successful_run(monkeypatch):
    import shorts_engine.cli as cli_module
    from shorts_engine.services.produce import Attempt, ProduceReport

    report = ProduceReport(requested=1, ready_job_ids=["job_1"], cost_idr=1550.0)
    report.attempts = [
        Attempt("Topic A", "job_9", "dropped", "x"),
        Attempt("Topic B", "job_1", "ready"),
    ]
    monkeypatch.setattr(cli_module, "produce_scripts", lambda db, count: report)
    monkeypatch.setattr(cli_module, "_open_session", lambda dry_run: create_engine_session())

    result = runner.invoke(app, ["produce", "--count", "1"])

    assert result.exit_code == 0, result.output
    assert "1. dibuang [job_9] Topic A (x)" in result.output
    assert "2. SIAP [job_1] Topic B" in result.output
    assert "Naskah siap rekam: 1 dari 1" in result.output


def create_engine_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


# ------------------------------------------------------------------ the style guide conflict


def test_no_series_hook_pattern_trips_the_overstatement_rule():
    config = get_config()
    words = config.settings["script"]["overstatement_words"]

    for series in config.topic_policy["series"]:
        found = find_unclaimed_overstatements(series["hook_pattern"], "", words)
        assert found == [], f"{series['name']} hook pattern would always be rejected: {found}"


def test_a_run_stops_starting_new_topics_once_its_cost_cap_is_reached(db_session, stages):
    from shorts_engine.services.produce import STOP_COST_CAP

    add_topic(db_session, "ok-a", score=9.0)
    add_topic(db_session, "ok-b", score=5.0)

    report = produce(db_session, 2, max_cost_idr=1000.0)  # one script costs Rp1,550 here

    assert len(report.ready_job_ids) == 1
    assert report.stopped == STOP_COST_CAP and "1,000" in report.stop_detail
    assert db_session.query(Topic).filter_by(text="ok-b").one().status == "queued"  # untouched


def test_without_a_cost_cap_the_run_goes_on(db_session, stages):
    add_topic(db_session, "ok-a", score=9.0)
    add_topic(db_session, "ok-b", score=5.0)

    report = produce(db_session, 2)

    assert len(report.ready_job_ids) == 2 and report.stopped is None
