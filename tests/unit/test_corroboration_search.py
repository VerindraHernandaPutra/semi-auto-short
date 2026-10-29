import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, CostEntry, Job, Source, Topic
from shorts_engine.domain.schemas import (
    ClaimCandidate,
    ClaimSupportCheck,
    CorroborationResult,
    ExtractedClaims,
)
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.corroboration import CorroborationSource, find_corroboration
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMUsage
from shorts_engine.providers.search.base import (
    BaseSearchProvider,
    SearchProviderError,
    SearchUsage,
)
from shorts_engine.services.costs import SearchQuotaExceeded

CLAIM = "Octopuses can change color quickly."
OTHER_FACTS = [
    "Chromatophores are pigment cells in octopus skin.",
    "Muscles expand the cells in octopus skin.",
]
TEXT_A = f"{CLAIM} " + " ".join(OTHER_FACTS) + " Padding text. " * 3
FILLER = "Researchers describe many details of octopus skin in long passages. " * 12
QUOTE = "octopus skin can change color quickly"
PAGE_NOAA = f"{FILLER} Notably, {QUOTE}, according to the study. {FILLER}"
MARKER_NOAA = "according to the study"


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


class ScriptedLLM(BaseLLMProvider):
    """Extraction, support and corroboration answers from a script. Corroboration answers are
    chosen by a marker that appears in the page text, so generated source ids do not matter."""

    def __init__(self, candidates, found_markers=None, support="supports"):
        self.candidates = candidates
        self.found_markers = found_markers or {}  # marker text -> exact quote
        self.support = support
        self.corroboration_prompts: list[str] = []
        self.calls = 0

    def generate_structured(self, prompt, schema, model_name, effort=None):
        self.calls += 1
        if self.usage_listener:
            self.usage_listener(LLMUsage("fake", model_name, schema.__name__, 100, 10))
        if schema is ExtractedClaims:
            return ExtractedClaims(claims=self.candidates)
        if schema is CorroborationResult:
            self.corroboration_prompts.append(prompt)
            for marker, quote in self.found_markers.items():
                if marker in prompt:
                    return CorroborationResult(found=True, supporting_quote=quote, reason="ok")
            return CorroborationResult(found=False, supporting_quote="", reason="not stated")
        if schema is ClaimSupportCheck:
            return ClaimSupportCheck(status=self.support, reason="scripted")
        raise AssertionError(schema)


class PagesSearch(BaseSearchProvider):
    def __init__(self, pages=None, error=None, credits=1):
        self.pages = pages or []
        self.error = error
        self.credits = credits
        self.queries: list[str] = []

    def search_and_extract(self, queries):
        self.queries += queries
        if self.error:
            raise self.error
        if self.usage_listener:
            self.usage_listener(SearchUsage("tavily", "search", self.credits))
        return self.pages


def page(url, content=PAGE_NOAA):
    host = url.split("/")[2]
    return {"url": url, "domain": host, "title": "T", "tier": "x", "content": content}


def setup_job(db, tmp_path, extra_sources=None):
    db.add(Topic(id="topic_1", text="Octopus color", category="ocean"))
    job = Job(id="job_1", topic_id="topic_1", status=JobState.VERIFYING.value)
    db.add(job)
    sources = {"source_a": ("nature.com", TEXT_A)}
    sources.update(extra_sources or {})
    for source_id, (domain, text) in sources.items():
        path = tmp_path / f"{source_id}.txt"
        path.write_text(text, encoding="utf-8")
        db.add(
            Source(
                id=source_id,
                job_id="job_1",
                url=f"https://{domain}/article",
                domain=domain,
                tier="tier_a",
                extracted_text_path=str(path),
            )
        )
    db.commit()
    return job


def candidates(hook_text=CLAIM):
    return [
        ClaimCandidate(
            text=hook_text, supporting_quote=hook_text, source_id="source_a", is_hook=True
        ),
        *[
            ClaimCandidate(text=fact, supporting_quote=fact, source_id="source_a")
            for fact in OTHER_FACTS
        ],
    ]


def verify(db, job, llm, search, config=None):
    return run_verify(db, job, llm=llm, search=search, config=config or get_config())


def read_log(data_dir):
    return json.loads((data_dir / "jobs" / "job_1" / "research" / "verification.json").read_text())


# ---------------------------------------------------------------- the claim-targeted search


def test_a_search_for_the_claim_supplies_the_missing_second_source(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})
    search = PagesSearch([page("https://www.noaa.gov/octopus")])

    assert verify(db_session, job, llm, search) is True

    assert search.queries == [CLAIM]  # the search was made for this exact claim
    hook = db_session.query(Claim).filter(Claim.is_hook.is_(True)).one()
    assert hook.text == CLAIM and len(hook.source_ids) == 2 and hook.source_ids[0] == "source_a"
    new_id = hook.source_ids[1]
    stored = db_session.query(Source).filter_by(id=new_id).one()
    assert (stored.domain, stored.tier, stored.url) == (
        "noaa.gov",
        "tier_a",
        "https://www.noaa.gov/octopus",
    )
    assert (data_dir / "jobs" / "job_1" / "research" / f"{new_id}.txt").is_file()
    assert f"[{new_id}] {QUOTE}" in hook.supporting_quote


def test_search_credits_are_recorded_against_the_job(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})

    verify(db_session, job, llm, PagesSearch([page("https://www.noaa.gov/octopus")], credits=1))

    rows = db_session.query(CostEntry).filter_by(provider="tavily").all()
    assert [(r.job_id, r.input_units) for r in rows] == [("job_1", 1)]


def test_a_page_from_the_same_organisation_never_counts(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})
    search = PagesSearch([page("https://research.nature.com/octopus")])  # same org as source_a

    assert verify(db_session, job, llm, search) is False

    assert llm.corroboration_prompts == []  # never even asked


def test_untrusted_pages_from_the_search_are_ignored(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})
    search = PagesSearch(
        [page("https://facebook.com/post"), page("https://en.wikipedia.org/wiki/X")]
    )

    assert verify(db_session, job, llm, search) is False

    assert llm.corroboration_prompts == []
    searched = read_log(data_dir)["hook_candidates"][0]["search"]
    assert searched["usable_domains"] == [] and "1 tier_c" in searched["rejected"]
    assert "1 unlisted" in searched["rejected"]


def test_a_page_that_is_already_a_source_is_not_used_twice(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path, {"source_b": ("noaa.gov", "Unrelated text. " * 60)})
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})
    search = PagesSearch([page("https://noaa.gov/article")])  # the URL of source_b

    assert verify(db_session, job, llm, search) is False

    # Three hook candidates, each asks source_b once. If the search page counted as a new
    # source it would be asked again for each of them: six prompts instead of three.
    assert len(llm.corroboration_prompts) == 3


def test_no_search_is_made_when_the_collected_pages_already_confirm(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path, {"source_b": ("noaa.gov", PAGE_NOAA)})
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})
    search = PagesSearch()

    assert verify(db_session, job, llm, search) is True

    assert search.queries == []  # the first pass was enough, so no credit was spent


def test_the_search_can_be_switched_off(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    config = get_config()
    config.settings["verification"]["corroboration_search"] = False
    search = PagesSearch([page("https://www.noaa.gov/octopus")])
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})

    assert verify(db_session, job, llm, search, config) is False

    assert search.queries == []


def test_the_number_of_searches_is_capped(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    config = get_config()
    config.settings["verification"]["max_corroboration_searches"] = 2
    search = PagesSearch([])  # finds nothing, so every hook candidate asks for another search

    assert verify(db_session, job, ScriptedLLM(candidates()), search, config) is False

    assert len(search.queries) == 2


def test_a_failing_search_fails_the_job_and_keeps_the_log(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    error = SearchProviderError("tavily", "plan usage limit reached", "quota")

    assert verify(db_session, job, ScriptedLLM(candidates()), PagesSearch(error=error)) is False

    assert job.status == JobState.FAILED.value
    assert db_session.query(Claim).count() == 0
    assert (data_dir / "jobs" / "job_1" / "research" / "verification.json").is_file()


def test_a_nearly_spent_search_quota_refuses_verification_before_any_paid_call(
    db_session, tmp_path, data_dir
):
    job = setup_job(db_session, tmp_path)
    db_session.add(CostEntry(provider="tavily", operation="search", input_units=898, cost_usd=0.0))
    db_session.commit()  # limit 900, and the run may need up to 4 credits
    llm = ScriptedLLM(candidates())

    with pytest.raises(SearchQuotaExceeded):
        verify(db_session, job, llm, PagesSearch())

    assert llm.calls == 0 and job.status == JobState.VERIFYING.value


def test_pages_that_confirmed_nothing_are_not_stored_as_sources(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    search = PagesSearch([page("https://www.noaa.gov/octopus")])

    verify(db_session, job, ScriptedLLM(candidates()), search)  # the model finds nothing

    assert db_session.query(Source).count() == 1  # only the original source


# ---------------------------------------------------------------- the audit log


def test_the_log_explains_each_attempt_and_the_search(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)
    llm = ScriptedLLM(candidates(), found_markers={MARKER_NOAA: QUOTE})

    verify(db_session, job, llm, PagesSearch([page("https://www.noaa.gov/octopus")]))

    entry = read_log(data_dir)["hook_candidates"][0]
    assert entry["claim"] == CLAIM and entry["supporting_domains"] == ["nature.com"]
    assert entry["search"]["query"] == CLAIM and entry["search"]["usable_domains"] == ["noaa.gov"]
    assert [a["outcome"] for a in entry["attempts"]] == ["confirmed"]
    assert entry["attempts"][0]["quote"] == QUOTE and entry["confirmed"] is True


def test_a_dropped_topic_leaves_a_log_that_says_why(db_session, tmp_path, data_dir):
    job = setup_job(db_session, tmp_path)

    verify(
        db_session, job, ScriptedLLM(candidates()), PagesSearch([page("https://www.noaa.gov/o")])
    )

    assert job.status == JobState.DROPPED.value
    entries = read_log(data_dir)["hook_candidates"]
    assert entries and all(e["confirmed"] is False for e in entries)
    assert entries[0]["attempts"][0]["outcome"] == "not_stated"


# ---------------------------------------------------------------- every failure has a name


class OneShotLLM(BaseLLMProvider):
    def __init__(self, answer, support="supports"):
        self.answer = answer
        self.support = support

    def generate_structured(self, prompt, schema, model_name, effort=None):
        if schema is CorroborationResult:
            return self.answer
        return ClaimSupportCheck(status=self.support, reason="r")


SOURCE = [CorroborationSource("s1", "noaa.gov", f"Octopus skin can change color quickly. {FILLER}")]


@pytest.mark.parametrize(
    ("answer", "support", "claim", "outcome"),
    [
        (
            CorroborationResult(found=False, supporting_quote="", reason="no"),
            "supports",
            CLAIM,
            "not_stated",
        ),
        (
            CorroborationResult(found=True, supporting_quote="  ", reason="x"),
            "supports",
            CLAIM,
            "empty_quote",
        ),
        (
            CorroborationResult(
                found=True, supporting_quote="octopus skin never changes", reason="x"
            ),
            "supports",
            CLAIM,
            "quote_not_in_source",
        ),
        (
            CorroborationResult(
                found=True, supporting_quote="Octopus skin can change color quickly", reason="x"
            ),
            "supports",
            "Octopus skin can change color in 3 seconds.",
            "number_mismatch",
        ),
        (
            CorroborationResult(
                found=True, supporting_quote="Octopus skin can change color quickly", reason="x"
            ),
            "partial",
            CLAIM,
            "support_partial",
        ),
        (
            CorroborationResult(
                found=True, supporting_quote="Octopus skin can change color quickly", reason="x"
            ),
            "supports",
            CLAIM,
            "confirmed",
        ),
    ],
)
def test_every_outcome_is_recorded_with_its_own_name(answer, support, claim, outcome):
    result = find_corroboration(
        claim, {"nature.com"}, SOURCE, OneShotLLM(answer, support), "m", 0.92
    )

    assert [a.outcome for a in result.attempts] == [outcome]
    assert bool(result.evidence) == (outcome == "confirmed")


def test_claims_that_another_source_seems_to_state_are_tried_as_hook_first(
    db_session, tmp_path, data_dir
):
    shared, other = OTHER_FACTS[1], OTHER_FACTS[0]
    page_b = f"{shared} {FILLER} Notably, {shared.lower()} according to the study. {FILLER}"
    job = setup_job(db_session, tmp_path, {"source_b": ("noaa.gov", page_b)})
    cands = [
        ClaimCandidate(text=CLAIM, supporting_quote=CLAIM, source_id="source_a", is_hook=True),
        ClaimCandidate(text=other, supporting_quote=other, source_id="source_a"),  # listed first
        ClaimCandidate(text=shared, supporting_quote=shared, source_id="source_a"),  # shared with b
        ClaimCandidate(
            text="Padding text.", supporting_quote="Padding text.", source_id="source_a"
        ),
    ]
    llm = ScriptedLLM(cands, found_markers={f"Claim: {shared}": shared.rstrip(".").lower()})

    assert verify(db_session, job, llm, PagesSearch()) is True

    tried = [p.split("Claim: ", 1)[1].splitlines()[0] for p in llm.corroboration_prompts]
    assert tried == [CLAIM, shared]  # the unshared claim was never spent on
    assert db_session.query(Claim).filter(Claim.is_hook.is_(True)).one().text == shared
