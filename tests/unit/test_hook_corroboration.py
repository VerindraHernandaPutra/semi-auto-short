import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, Job, Source, Topic
from shorts_engine.domain.schemas import (
    ClaimCandidate,
    ClaimSupportCheck,
    CorroborationResult,
    ExtractedClaims,
    ScriptOutput,
    ScriptSegment,
)
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.corroboration import CorroborationSource, find_corroboration
from shorts_engine.pipeline.script import _validate_references
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError
from shorts_engine.providers.search.base import BaseSearchProvider

CLAIM = "Octopuses can change color quickly."
TEXT_A = f"{CLAIM} Chromatophores are pigment cells in octopus skin. Padding text here."
TEXT_B = "Researchers note that octopus skin can change color quickly. Muscles expand the cells."
QUOTE_B = "octopus skin can change color quickly"


class ScriptedLLM(BaseLLMProvider):
    """Answers each schema from a script and counts calls, so tests can assert on cost."""

    def __init__(self, candidates=None, found=None, support="supports", corroboration_error=None):
        self.candidates = candidates or []
        self.found = found or {}  # source id -> quote the model claims to have found
        self.support = support
        self.corroboration_error = corroboration_error
        self.corroboration_calls: list[str] = []
        self.support_calls = 0

    def generate_structured(self, prompt, schema, model_name, effort=None):
        if schema is ExtractedClaims:
            return ExtractedClaims(claims=self.candidates)
        if schema is CorroborationResult:
            source_id = re.search(r"--- Source (\S+) ---", prompt).group(1)
            self.corroboration_calls.append(source_id)
            if self.corroboration_error:
                raise self.corroboration_error
            quote = self.found.get(source_id)
            return CorroborationResult(
                found=quote is not None, supporting_quote=quote or "", reason="scripted"
            )
        if schema is ClaimSupportCheck:
            self.support_calls += 1
            return ClaimSupportCheck(status=self.support, reason="scripted")
        raise AssertionError(f"unexpected schema {schema.__name__}")


def sources(*pairs):
    return [CorroborationSource(f"s_{domain}", domain, text) for domain, text in pairs]


def corroborate(llm, candidates, covered=("nature.com",), claim=CLAIM, wanted=1):
    return find_corroboration(claim, set(covered), candidates, llm, "m", 0.92, wanted)


# ---------------------------------------------------------------- the corroboration step


def test_a_second_independent_source_is_confirmed_with_its_own_quote():
    pool = sources(("noaa.gov", TEXT_B))
    llm = ScriptedLLM(found={"s_noaa.gov": QUOTE_B})

    [evidence] = corroborate(llm, pool)

    assert (evidence.source_id, evidence.domain, evidence.quote) == (
        "s_noaa.gov",
        "noaa.gov",
        QUOTE_B,
    )
    assert evidence.quote_score == 1.0


def test_domains_already_covered_are_never_asked():
    pool = sources(("nature.com", TEXT_A), ("noaa.gov", TEXT_B))
    llm = ScriptedLLM(found={"s_noaa.gov": QUOTE_B})

    corroborate(llm, pool)

    assert llm.corroboration_calls == ["s_noaa.gov"]


def test_search_stops_once_enough_domains_are_confirmed():
    pool = sources(("noaa.gov", TEXT_B), ("nasa.gov", TEXT_B), ("mit.edu", TEXT_B))
    llm = ScriptedLLM(found={f"s_{d}": QUOTE_B for d in ("noaa.gov", "nasa.gov", "mit.edu")})

    evidence = corroborate(llm, pool, wanted=1)

    assert len(evidence) == 1 and llm.corroboration_calls == ["s_noaa.gov"]


def test_each_independence_domain_gets_one_attempt_only():
    pool = [
        CorroborationSource("s1", "noaa.gov", "nothing relevant here"),
        CorroborationSource("s2", "noaa.gov", TEXT_B),  # same organisation: not tried again
    ]
    llm = ScriptedLLM(found={"s2": QUOTE_B})

    assert corroborate(llm, pool) == []
    assert llm.corroboration_calls == ["s1"]


def test_a_quote_that_is_not_in_the_source_is_rejected_even_if_the_model_insists():
    pool = sources(("noaa.gov", TEXT_B))
    llm = ScriptedLLM(found={"s_noaa.gov": "octopuses always change color within one second"})

    assert corroborate(llm, pool) == []
    assert llm.support_calls == 0  # rejected before the second model is even asked


def test_a_quote_with_a_different_number_is_rejected():
    claim = "An octopus has three hearts."
    pool = sources(("noaa.gov", "An octopus has two hearts, researchers report."))
    llm = ScriptedLLM(found={"s_noaa.gov": "An octopus has two hearts"})

    assert corroborate(llm, pool, claim=claim) == []


def test_a_partial_support_verdict_is_not_enough():
    pool = sources(("noaa.gov", TEXT_B))
    llm = ScriptedLLM(found={"s_noaa.gov": QUOTE_B}, support="partial")

    assert corroborate(llm, pool) == []


def test_found_without_a_quote_is_rejected():
    pool = sources(("noaa.gov", TEXT_B))
    llm = ScriptedLLM(found={"s_noaa.gov": "   "})

    assert corroborate(llm, pool) == []


# ---------------------------------------------------------------- inside verification


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def setup_job(db, tmp_path, texts):
    db.add(Topic(id="topic_1", text="Octopus color", category="ocean"))
    job = Job(id="job_1", topic_id="topic_1", status=JobState.VERIFYING.value)
    db.add(job)
    for source_id, (domain, text) in texts.items():
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


def candidate(text, source_id, hook=False, quote=None):
    return ClaimCandidate(
        text=text, supporting_quote=quote or text, source_id=source_id, is_hook=hook
    )


BASE_CANDIDATES = [
    candidate(CLAIM, "source_a", hook=True),
    candidate("Chromatophores are pigment cells in octopus skin.", "source_a"),
    candidate("Muscles expand the cells.", "source_b", quote="Muscles expand the cells."),
]
TWO_SOURCES = {"source_a": ("nature.com", TEXT_A), "source_b": ("noaa.gov", TEXT_B)}


class NullSearch(BaseSearchProvider):
    def search_and_extract(self, queries):
        return []


def verify(db, job, llm):
    return run_verify(db, job, llm=llm, search=NullSearch(), config=get_config())


def test_hook_stated_by_one_source_is_confirmed_through_corroboration(db_session, tmp_path):
    job = setup_job(db_session, tmp_path, TWO_SOURCES)
    llm = ScriptedLLM(BASE_CANDIDATES, found={"source_b": QUOTE_B})

    assert verify(db_session, job, llm) is True

    hook = db_session.query(Claim).filter(Claim.is_hook.is_(True)).one()
    assert hook.status == "verified"
    assert hook.source_ids == ["source_a", "source_b"]  # both supporting sources are kept
    assert (
        "[source_a]" in hook.supporting_quote and f"[source_b] {QUOTE_B}" in hook.supporting_quote
    )
    assert hook.quote_match_score == 1.0


def test_hook_without_a_second_source_is_dropped_and_recorded(db_session, tmp_path):
    job = setup_job(db_session, tmp_path, TWO_SOURCES)
    llm = ScriptedLLM(BASE_CANDIDATES, found={})

    assert verify(db_session, job, llm) is False

    rejected = db_session.query(Claim).filter(Claim.reject_reason.is_not(None)).all()
    assert [c.reject_reason for c in rejected] == ["hook_requires_two_independent_domains"]
    assert job.status == JobState.DROPPED.value


def test_corroboration_failure_fails_the_job_instead_of_guessing(db_session, tmp_path):
    job = setup_job(db_session, tmp_path, TWO_SOURCES)
    llm = ScriptedLLM(BASE_CANDIDATES, corroboration_error=LLMProviderError("fake", "refused"))

    assert verify(db_session, job, llm) is False

    assert job.status == JobState.FAILED.value
    assert db_session.query(Claim).count() == 0  # nothing half-verified was stored


def test_two_sources_of_the_same_organisation_do_not_corroborate_each_other(db_session, tmp_path):
    texts = {"source_a": ("nature.com", TEXT_A), "source_b": ("research.nature.com", TEXT_B)}
    job = setup_job(db_session, tmp_path, texts)
    llm = ScriptedLLM(BASE_CANDIDATES, found={"source_b": QUOTE_B})

    assert verify(db_session, job, llm) is False
    assert llm.corroboration_calls == []  # same independence domain: never even asked


def test_model_that_restates_the_hook_per_source_needs_no_extra_calls(db_session, tmp_path):
    job = setup_job(db_session, tmp_path, TWO_SOURCES)
    restated = candidate(CLAIM, "source_b", hook=True, quote=QUOTE_B)
    llm = ScriptedLLM([*BASE_CANDIDATES, restated], found={})

    assert verify(db_session, job, llm) is True

    assert llm.corroboration_calls == []  # two independent domains already, no extra search
    assert db_session.query(Claim).filter(Claim.is_hook.is_(True)).count() == 2


def test_only_one_hook_is_confirmed_and_extra_hook_flags_stay_ordinary_claims(db_session, tmp_path):
    job = setup_job(db_session, tmp_path, TWO_SOURCES)
    second_hook = candidate(
        "Chromatophores are pigment cells in octopus skin.", "source_a", hook=True
    )
    candidates = [BASE_CANDIDATES[0], second_hook, BASE_CANDIDATES[2]]
    llm = ScriptedLLM(candidates, found={"source_b": QUOTE_B})

    assert verify(db_session, job, llm) is True

    assert llm.corroboration_calls == ["source_b"]  # the second hook group was never tried
    assert db_session.query(Claim).filter(Claim.is_hook.is_(True)).count() == 1
    ordinary = db_session.query(Claim).filter(Claim.text.like("Chromatophores%")).one()
    assert ordinary.status == "verified" and ordinary.is_hook is False


def test_the_number_of_hook_candidates_tried_is_capped(db_session, tmp_path):
    job = setup_job(db_session, tmp_path, TWO_SOURCES)
    extra = [candidate(f"Fact {i} about octopus skin.", "source_a", hook=True) for i in range(3)]
    texts = TEXT_A + " " + " ".join(f"Fact {i} about octopus skin." for i in range(3))
    (tmp_path / "source_a.txt").write_text(texts, encoding="utf-8")
    llm = ScriptedLLM([*extra, BASE_CANDIDATES[2]], found={})

    assert verify(db_session, job, llm) is False

    assert len(llm.corroboration_calls) == 2  # max_hook_candidates, one other source each


# ---------------------------------------------------------------- the script respects the rule


def hook_script(hook_ids):
    return ScriptOutput(
        title_working="A title",
        hook="It dives 30 meters.",
        hook_claim_ids=hook_ids,
        segments=[
            ScriptSegment(
                text="It dives 30 meters.",
                claim_ids=["claim_1"],
                kind="factual",
                visual_suggestion="A whale",
            )
        ],
        ending="A quiet ending.",
        story_structure="observation-mechanism-conclusion",
        series="Strange Creatures",
        estimated_duration_s=40,
    )


def make_claim(is_hook):
    return Claim(
        id="claim_1",
        job_id="job_1",
        source_ids=["s"],
        text="It dives 30 meters.",
        supporting_quote="It dives 30 meters.",
        quote_match_score=1.0,
        support_check="supports",
        status="verified",
        is_hook=is_hook,
    )


def test_a_script_cannot_open_with_a_claim_that_was_not_confirmed_by_two_sources():
    error = _validate_references(hook_script(["claim_1"]), {"claim_1": make_claim(False)})

    assert error == "Hook must rest on a claim confirmed by two independent sources"


def test_a_script_may_open_with_a_confirmed_hook_claim():
    assert _validate_references(hook_script(["claim_1"]), {"claim_1": make_claim(True)}) is None
