from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from tavily.errors import (
    BadRequestError,
    InvalidAPIKeyError,
    UsageLimitExceededError,
)
from tavily.errors import TimeoutError as TavilyTimeoutError

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, CostEntry, Job, Source, Topic
from shorts_engine.domain.schemas import ExtractedClaims, ScriptOutput, SearchQueries
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.research import (
    build_query_prompt,
    clean_queries,
    run_research,
    select_sources,
)
from shorts_engine.pipeline.script import run_script
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError, LLMUsage
from shorts_engine.providers.search.base import (
    BaseSearchProvider,
    SearchProviderError,
    SearchUsage,
)
from shorts_engine.providers.search.tavily_search import TavilyProvider
from shorts_engine.services.costs import (
    BudgetExceeded,
    SearchQuotaExceeded,
    check_search_quota,
    cost_report,
    record_search_usage,
    track_costs,
)

WHITELIST = {
    "tier_a": ["nasa.gov", "nature.com", ".edu"],
    "tier_b": ["bbc.com"],
    "tier_c": ["wikipedia.org"],
}
LONG_TEXT = "Mantis shrimp strike with club-like limbs at very high speed. " * 20


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def page(url, tier="tier_a", content=LONG_TEXT, title="T"):
    return {
        "url": url,
        "domain": url.split("/")[2],
        "title": title,
        "tier": tier,
        "content": content,
    }


# ---------------------------------------------------------------- source selection


def test_only_tier_a_and_b_pages_are_kept_and_tier_comes_from_the_url():
    results = [
        page("https://en.wikipedia.org/wiki/Mantis_shrimp", tier="tier_a"),  # provider lies
        page("https://blog.example.com/post"),
        page("https://www.nasa.gov/a"),
        page("https://www.bbc.com/b", tier="tier_c"),  # provider lies the other way
    ]

    chosen = select_sources(results, WHITELIST, 6, 2, 10_000)

    assert [(c["domain"], c["tier"]) for c in chosen] == [
        ("nasa.gov", "tier_a"),
        ("bbc.com", "tier_b"),
    ]


def test_duplicate_urls_are_merged_even_with_www_and_trailing_slash():
    results = [
        page("https://www.nasa.gov/a/"),
        page("https://nasa.gov/a"),
        page("https://nasa.gov/b"),
    ]

    chosen = select_sources(results, WHITELIST, 6, 5, 10_000)

    assert [c["url"] for c in chosen] == ["https://www.nasa.gov/a/", "https://nasa.gov/b"]


def test_per_domain_and_total_caps_keep_the_evidence_diverse():
    results = [page(f"https://nasa.gov/{i}") for i in range(5)] + [
        page("https://nature.com/x"),
        page("https://mit.edu/y"),
        page("https://bbc.com/z", tier="tier_b"),
    ]

    chosen = select_sources(results, WHITELIST, 4, 2, 10_000)

    assert len(chosen) == 4
    assert sum("nasa.gov" in c["url"] for c in chosen) == 2


def test_tier_a_ranks_before_tier_b_and_text_is_truncated():
    results = [page("https://bbc.com/z", tier="tier_b"), page("https://nasa.gov/a")]

    chosen = select_sources(results, WHITELIST, 6, 2, 100)

    assert chosen[0]["tier"] == "tier_a"
    assert all(len(c["content"]) == 100 for c in chosen)


def test_pages_without_real_content_are_dropped():
    assert select_sources([page("https://nasa.gov/a", content="menu")], WHITELIST, 6, 2, 100) == []


def test_queries_are_cleaned_deduplicated_and_capped():
    cleaned = clean_queries(["  mantis   shrimp ", "Mantis Shrimp", "", "q3", "q4"], 3)

    assert cleaned == ["mantis shrimp", "q3", "q4"]


def test_query_prompt_is_english_and_treats_the_topic_as_a_hypothesis():
    prompt = build_query_prompt("Why X happens", 6)

    assert "hypothesis" in prompt and "Why X happens" in prompt and "3 to 6" in prompt
    assert "Buat" not in prompt


# ---------------------------------------------------------------- the research stage


class FakeLLM(BaseLLMProvider):
    def __init__(self, queries=None, error=None):
        self.queries = ["q one", "q two"] if queries is None else queries
        self.error = error
        self.calls = 0

    def generate_structured(self, prompt, schema, model_name, effort=None):
        self.calls += 1
        if self.usage_listener:
            self.usage_listener(LLMUsage("fake", model_name, schema.__name__, 1000, 200))
        if self.error:
            raise self.error
        assert schema is SearchQueries
        return SearchQueries(queries=self.queries)


class FakeSearch(BaseSearchProvider):
    def __init__(self, results=None, error=None, credits_per_query=1):
        self.results = results or []
        self.error = error
        self.credits = credits_per_query
        self.queries_seen = None

    def search_and_extract(self, queries):
        self.queries_seen = queries
        if self.error:
            raise self.error
        if self.usage_listener:
            for _ in queries:
                self.usage_listener(SearchUsage("tavily", "search", self.credits))
        return self.results


def make_job(db, status=JobState.TOPIC_QUEUED):
    db.add(Topic(id="t1", text="How a mantis shrimp strikes", category="ocean", status="used"))
    job = Job(id="job_1", topic_id="t1", status=status.value)
    db.add(job)
    db.commit()
    return job


def research(db, job, llm=None, search=None):
    return run_research(
        db, job, llm=llm or FakeLLM(), search=search or FakeSearch(), config=get_config()
    )


GOOD_RESULTS = [
    page("https://www.nasa.gov/a"),
    page("https://www.nature.com/b"),
    page("https://wikipedia.org/w"),
]


def test_research_stores_only_selected_sources_and_moves_to_verification(db_session, data_dir):
    job = make_job(db_session)

    assert research(db_session, job, search=FakeSearch(GOOD_RESULTS)) is True

    assert job.status == JobState.VERIFYING.value
    sources = db_session.query(Source).all()
    assert sorted(s.domain for s in sources) == ["nasa.gov", "nature.com"]
    for source in sources:
        assert (data_dir / "jobs" / "job_1" / "research" / f"{source.id}.txt").is_file()
        assert source.extracted_text_path.endswith(f"{source.id}.txt")


def test_research_records_llm_and_search_costs_against_the_job(db_session, data_dir):
    job = make_job(db_session)

    research(db_session, job, search=FakeSearch(GOOD_RESULTS))

    entries = db_session.query(CostEntry).all()
    assert {e.job_id for e in entries} == {"job_1"}
    assert [e.operation for e in entries if e.provider == "fake"] == ["SearchQueries"]
    search_rows = [e for e in entries if e.provider == "tavily"]
    assert len(search_rows) == 2  # one row per query
    assert sum(e.input_units for e in search_rows) == 2  # one credit each


def test_research_caps_the_number_of_queries_sent_to_search(db_session, data_dir):
    job = make_job(db_session)
    search = FakeSearch(GOOD_RESULTS)

    research(db_session, job, llm=FakeLLM([f"query {i}" for i in range(12)]), search=search)

    assert len(search.queries_seen) == 6


def test_a_single_domain_is_dropped_before_the_expensive_extraction(db_session, data_dir):
    job = make_job(db_session)

    assert research(db_session, job, search=FakeSearch([page("https://nasa.gov/a")])) is False

    assert job.status == JobState.DROPPED.value
    assert db_session.query(Source).count() == 0


def test_tier_c_only_results_are_dropped(db_session, data_dir):
    job = make_job(db_session)

    assert research(db_session, job, search=FakeSearch([page("https://wikipedia.org/w")])) is False
    assert job.status == JobState.DROPPED.value


def test_search_failure_fails_the_job_instead_of_dropping_the_topic(db_session, data_dir):
    job = make_job(db_session)
    error = SearchProviderError("tavily", "authentication or permission failed", "auth")

    assert research(db_session, job, search=FakeSearch(error=error)) is False

    assert job.status == JobState.FAILED.value  # not "dropped": the topic itself was fine
    assert "authentication" in job.error if job.error else True


def test_query_generation_failure_fails_the_job(db_session, data_dir):
    job = make_job(db_session)

    assert research(db_session, job, llm=FakeLLM(error=LLMProviderError("fake", "boom"))) is False
    assert job.status == JobState.FAILED.value


def test_empty_query_list_fails_the_job(db_session, data_dir):
    job = make_job(db_session)

    assert research(db_session, job, llm=FakeLLM(queries=["  ", ""])) is False
    assert job.status == JobState.FAILED.value


def test_spent_budget_stops_research_before_any_call_and_leaves_the_job_resumable(
    db_session, data_dir
):
    job = make_job(db_session)
    db_session.add(CostEntry(provider="anthropic", operation="x", cost_usd=100.0))
    db_session.commit()
    llm = FakeLLM()

    with pytest.raises(BudgetExceeded):
        research(db_session, job, llm=llm)

    assert llm.calls == 0
    assert job.status == JobState.TOPIC_QUEUED.value


def test_search_quota_stops_research_before_any_call(db_session, data_dir):
    job = make_job(db_session)
    db_session.add(CostEntry(provider="tavily", operation="search", input_units=897, cost_usd=0.0))
    db_session.commit()  # 897 used of 900, and a run may need up to 6 credits
    llm = FakeLLM()

    with pytest.raises(SearchQuotaExceeded):
        research(db_session, job, llm=llm)

    assert llm.calls == 0 and job.status == JobState.TOPIC_QUEUED.value


# ---------------------------------------------------------------- cost helpers


def test_search_credits_are_recorded_and_count_toward_the_quota(db_session):
    record_search_usage(db_session, get_config(), SearchUsage("tavily", "search", 5), "job_1")

    assert check_search_quota(db_session, get_config(), needed=0) == 5
    with pytest.raises(SearchQuotaExceeded):
        check_search_quota(db_session, get_config(), needed=896)


def test_track_costs_restores_listeners_even_after_an_error(db_session):
    llm, search = FakeLLM(), FakeSearch()
    sentinel = object()
    llm.usage_listener = sentinel

    with (
        pytest.raises(RuntimeError),
        track_costs(db_session, get_config(), "j", llm=llm, search=search),
    ):
        assert llm.usage_listener is not sentinel
        raise RuntimeError("stage failed")

    assert llm.usage_listener is sentinel
    assert search.usage_listener is None


def test_cost_report_shows_search_credits_separately_from_tokens(db_session):
    record_search_usage(db_session, get_config(), SearchUsage("tavily", "search", 3))

    [item] = cost_report(db_session, get_config()).operations

    assert (item.provider, item.operation, item.input_tokens) == ("tavily", "search", 3)


# ---------------------------------------------------------------- verify and script costs


def test_verification_records_cost_against_the_job_and_respects_the_budget(db_session, tmp_path):
    job = make_job(db_session, JobState.VERIFYING)
    text_file = tmp_path / "s.txt"
    text_file.write_text(LONG_TEXT, encoding="utf-8")
    db_session.add(
        Source(
            id="s1",
            job_id="job_1",
            url="https://www.nasa.gov/a",
            domain="nasa.gov",
            tier="tier_a",
            extracted_text_path=str(text_file),
        )
    )
    db_session.commit()

    class ExtractLLM(FakeLLM):
        def generate_structured(self, prompt, schema, model_name, effort=None):
            self.calls += 1
            self.usage_listener(LLMUsage("fake", model_name, schema.__name__, 5000, 300))
            assert schema is ExtractedClaims
            return ExtractedClaims(claims=[])

    llm = ExtractLLM()
    assert run_verify(db_session, job, llm=llm, search=FakeSearch(), config=get_config()) is False
    entry = db_session.query(CostEntry).one()
    assert (entry.job_id, entry.operation) == ("job_1", "ExtractedClaims")

    db_session.add(CostEntry(provider="anthropic", operation="x", job_id="job_1", cost_usd=1.0))
    db_session.commit()  # now above Rp5.000 for this job
    job.status = JobState.VERIFYING.value
    with pytest.raises(BudgetExceeded):
        run_verify(db_session, job, llm=llm, search=FakeSearch(), config=get_config())
    assert llm.calls == 1  # no second call was made


def test_script_generation_cost_is_recorded_even_when_it_fails(db_session):
    job = make_job(db_session, JobState.VERIFYING)
    db_session.add(
        Claim(
            id="c1",
            job_id="job_1",
            source_ids=["s1"],
            text="A claim",
            supporting_quote="q",
            quote_match_score=1.0,
            support_check="supports",
            status="verified",
        )
    )
    db_session.commit()

    class FailingLLM(FakeLLM):
        def generate_structured(self, prompt, schema, model_name, effort=None):
            self.usage_listener(LLMUsage("fake", model_name, schema.__name__, 800, 100))
            assert schema is ScriptOutput
            raise LLMProviderError("fake", "refused")

    assert run_script(db_session, job, llm=FailingLLM(), config=get_config()) is False

    assert job.status == JobState.FAILED.value
    assert db_session.query(CostEntry).filter_by(job_id="job_1").count() == 1


# ---------------------------------------------------------------- the Tavily provider


class FakeTavilyClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def tavily(outcomes):
    client = FakeTavilyClient(outcomes)
    return TavilyProvider(client=client), client


OK = {
    "results": [{"url": "https://www.nasa.gov/x", "title": "T", "raw_content": "body"}],
    "usage": {"credits": 1},
}


def test_auth_failure_is_raised_without_retry_or_leaking_details():
    provider, client = tavily([InvalidAPIKeyError("secret-key-123 is invalid")])

    with pytest.raises(SearchProviderError) as raised:
        provider.search_and_extract(["q"])

    assert raised.value.kind == "auth"
    assert "secret-key-123" not in str(raised.value)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("error", "kind"),
    [(UsageLimitExceededError("limit"), "quota"), (BadRequestError("bad"), "bad_request")],
)
def test_quota_and_bad_requests_stop_the_whole_search(error, kind):
    provider, client = tavily([error, OK])

    with pytest.raises(SearchProviderError) as raised:
        provider.search_and_extract(["q1", "q2"])

    assert raised.value.kind == kind
    assert len(client.calls) == 1  # the second query was never attempted


def test_a_timeout_is_retried_once_then_succeeds():
    provider, client = tavily([TavilyTimeoutError(30), OK])

    results = provider.search_and_extract(["q"])

    assert len(client.calls) == 2
    assert results[0]["domain"] == "nasa.gov"


def test_one_failing_query_does_not_discard_the_others():
    provider, _ = tavily([TavilyTimeoutError(30), TavilyTimeoutError(30), OK])

    results = provider.search_and_extract(["slow query", "good query"])

    assert len(results) == 1


def test_all_queries_failing_is_an_error_not_an_empty_result():
    provider, _ = tavily([TavilyTimeoutError(30)] * 4)

    with pytest.raises(SearchProviderError) as raised:
        provider.search_and_extract(["a", "b"])

    assert raised.value.kind == "network"


def test_credits_are_reported_from_the_response_and_default_to_depth_cost():
    provider, _ = tavily([OK, {"results": []}])
    seen = []
    provider.usage_listener = seen.append

    provider.search_and_extract(["a", "b"])

    assert [u.credits for u in seen] == [1, 1]  # second response had no usage: basic costs 1


def test_searches_are_bounded_by_a_timeout_and_ask_for_usage():
    provider, client = tavily([OK])

    provider.search_and_extract(["q"])

    assert client.calls[0]["timeout"] == 30.0
    assert client.calls[0]["include_usage"] is True
    assert SimpleNamespace(**client.calls[0]).search_depth == "basic"
