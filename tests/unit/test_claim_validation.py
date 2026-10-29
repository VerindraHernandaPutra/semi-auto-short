import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, Job, JobEvent, Source, Topic
from shorts_engine.domain.schemas import (
    ClaimCandidate,
    ClaimSupportCheck,
    CorroborationResult,
    ExtractedClaims,
    ScriptClaimCheck,
    ScriptOutput,
    ScriptSegment,
)
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.claim_validation import (
    numeric_facts_are_supported,
    quote_similarity,
)
from shorts_engine.pipeline.script import _validate_references, run_script
from shorts_engine.pipeline.verify import run_verify
from shorts_engine.providers import factory
from shorts_engine.providers.search.base import BaseSearchProvider
from shorts_engine.providers.search.tiers import (
    classify_domain,
    source_independence_domain,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_job(session, status=JobState.VERIFYING):
    topic = Topic(id="topic_1", text="Test topic", category="animals")
    session.add(topic)
    job = Job(id="job_1", topic_id=topic.id, status=status.value)
    session.add(job)
    session.commit()
    return job


def test_quote_similarity_matches_normalized_quote_in_source():
    assert (
        quote_similarity("A whale can dive 30 meters below.", "A WHALE can dive 30 meters") == 1.0
    )


def test_quote_similarity_rejects_unrelated_quote():
    assert quote_similarity("The bird nests in trees.", "The whale swims in deep water.") < 0.92


@pytest.mark.parametrize(
    ("claim", "quote", "supported"),
    [
        ("It dives 30 meters.", "It dives 30 meters.", True),
        ("It dives 30 meters.", "It dives 30 seconds.", False),
        ("It dives 30 meters.", "It dives deep.", False),
        ("It dives three meters.", "It dives two meters.", False),
        ("It dives deep.", "It dives deep.", True),
        ("Some species have more than 10 pigments.", "Some have more than ten pigments.", True),
        ("It has ten arms.", "It has 10 arms.", True),
        ("It has twelve arms.", "It has ten arms.", False),
        ("It lays a dozen eggs.", "It lays 12 eggs.", True),
        ("It lays 3 eggs.", "It lays three eggs.", True),
        # The same measurement written another way is still the same measurement.
        ("It reaches 23 meters per second.", "Speeds of 12-23 m/s in water.", True),
        ("Its club moves at 12 to 23 meters per second.", "Speeds of 12-23 m/s in water.", True),
        ("It moves 23 kilometers per hour.", "Speeds of 12-23 m/s in water.", False),
        ("It travels five feet.", "It travels 5 ft.", True),
        ("It waits ten seconds.", "It waits 10 s.", True),
        ("It weighs 3 kilograms.", "It weighs 3 kg.", True),
        ("It rests 30 minutes.", "It rests 30 min.", True),
        ("It is 1 foot long.", "It is one foot long.", True),
        ("It is 2 feet long.", "It is one foot long.", False),
        # "one" as a pronoun is not a measurement; "one meter" still is.
        ("It is one of the fastest strikes.", "Speeds of 12-23 m/s in water.", True),
        ("In one strike the club reaches 23 m/s.", "Speeds of 12-23 m/s in water.", True),
        ("It travels one meter.", "It travels 2 meters.", False),
        ("It travels one meter.", "It travels 1 meter.", True),
    ],
)
def test_numeric_facts_require_matching_number_and_unit(claim, quote, supported):
    assert numeric_facts_are_supported(claim, quote) is supported


def test_source_tiers_match_exact_domains_and_subdomains_only():
    whitelist = {"tier_a": ["nature.com", ".edu"], "tier_b": ["bbc.com"]}

    assert classify_domain("www.nature.com", whitelist) == "tier_a"
    assert classify_domain("research.nature.com", whitelist) == "tier_a"
    assert classify_domain("nature.com.attacker.example", whitelist) == "unlisted"
    assert classify_domain("university.edu", whitelist) == "tier_a"
    assert source_independence_domain("science.nature.com", whitelist) == "nature.com"
    assert source_independence_domain("biology.mit.edu", whitelist) == "mit.edu"
    assert source_independence_domain("physics.caltech.edu", whitelist) == "caltech.edu"


def test_unknown_provider_selection_is_rejected_without_falling_back_to_mock():
    factory.set_active_providers("anthropic", "tavily")

    with pytest.raises(ValueError, match="Provider llm tidak dikenal"):
        factory.set_active_providers("invalid", "mock")

    names = factory.active_provider_names()
    assert names["llm"] == "anthropic"
    assert names["search"] == "tavily"


def test_mock_providers_drop_fact_verification_job(db_session, monkeypatch):
    import shorts_engine.pipeline.verify as verify_module
    from shorts_engine.providers.llm.mock import MockLLMProvider
    from shorts_engine.providers.search.mock import MockSearchProvider

    job = make_job(db_session)
    monkeypatch.setattr(verify_module, "get_llm_provider", MockLLMProvider)
    monkeypatch.setattr(verify_module, "get_search_provider", MockSearchProvider)

    assert run_verify(db_session, job) is False
    assert job.status == JobState.DROPPED.value
    assert job.error is None
    event = db_session.query(JobEvent).one()
    assert event.reason == "mock_provider_not_allowed_for_fact_verification"


def test_mock_provider_cannot_create_recordable_script(db_session, monkeypatch):
    import shorts_engine.pipeline.script as script_module
    from shorts_engine.providers.llm.mock import MockLLMProvider

    job = make_job(db_session)
    monkeypatch.setattr(script_module, "get_llm_provider", MockLLMProvider)

    assert run_script(db_session, job) is False
    assert job.status == JobState.DROPPED.value
    event = db_session.query(JobEvent).one()
    assert event.reason == "mock_provider_not_allowed_for_production_script"


class NullSearch(BaseSearchProvider):
    def search_and_extract(self, queries):
        return []


class VerifiedFakeLLM:
    def __init__(self, candidates):
        self.candidates = candidates

    def generate_structured(self, prompt, schema, model_name):
        if schema is ExtractedClaims:
            return ExtractedClaims(claims=self.candidates)
        if schema is ClaimSupportCheck:
            return ClaimSupportCheck(status="supports", reason="Supported by quote")
        if schema is CorroborationResult:
            return CorroborationResult(found=False, supporting_quote="", reason="not stated")
        raise AssertionError(f"Unexpected schema requested: {schema.__name__}")


def add_source(db_session, source_id, domain, text, tmp_path):
    source_file = tmp_path / f"{source_id}.txt"
    source_file.write_text(text, encoding="utf-8")
    db_session.add(
        Source(
            id=source_id,
            job_id="job_1",
            url=f"https://{domain}/article",
            domain=domain,
            title="Research source",
            tier="tier_a",
            extracted_text_path=str(source_file),
        )
    )


def test_claim_verification_requires_quotes_and_independent_hook_domains(
    db_session, monkeypatch, tmp_path
):
    import shorts_engine.pipeline.verify as verify_module

    job = make_job(db_session)
    hook = "Octopuses can change color quickly."
    source_text = (
        f"{hook} Chromatophores are pigment cells in octopus skin. "
        "Muscles control chromatophore expansion."
    )
    add_source(db_session, "source_a", "nature.com", source_text, tmp_path)
    add_source(db_session, "source_b", "noaa.gov", source_text, tmp_path)
    db_session.commit()

    candidates = [
        ClaimCandidate(
            text=hook,
            supporting_quote=hook,
            source_id="source_a",
            is_hook=True,
        ),
        ClaimCandidate(
            text=hook,
            supporting_quote=hook,
            source_id="source_b",
            is_hook=True,
        ),
        ClaimCandidate(
            text="Chromatophores are pigment cells in octopus skin.",
            supporting_quote="Chromatophores are pigment cells in octopus skin.",
            source_id="source_a",
        ),
        ClaimCandidate(
            text="Muscles control chromatophore expansion.",
            supporting_quote="Muscles control chromatophore expansion.",
            source_id="source_b",
        ),
    ]
    monkeypatch.setattr(verify_module, "get_llm_provider", lambda: VerifiedFakeLLM(candidates))
    monkeypatch.setattr(verify_module, "get_search_provider", lambda: NullSearch())

    assert run_verify(db_session, job) is True
    verified_claims = (
        db_session.query(Claim).filter(Claim.job_id == job.id, Claim.status == "verified").all()
    )
    assert len(verified_claims) == 4
    assert sum(claim.is_hook for claim in verified_claims) == 2


def test_hook_claim_from_one_domain_is_not_verified(db_session, monkeypatch, tmp_path):
    import shorts_engine.pipeline.verify as verify_module

    job = make_job(db_session)
    hook = "Octopuses can change color quickly."
    source_text = (
        f"{hook} Chromatophores are pigment cells in octopus skin. "
        "Muscles control chromatophore expansion."
    )
    add_source(db_session, "source_a", "nature.com", source_text, tmp_path)
    add_source(db_session, "source_b", "noaa.gov", source_text, tmp_path)
    db_session.commit()

    candidates = [
        ClaimCandidate(
            text=hook,
            supporting_quote=hook,
            source_id="source_a",
            is_hook=True,
        ),
        ClaimCandidate(
            text="Chromatophores are pigment cells in octopus skin.",
            supporting_quote="Chromatophores are pigment cells in octopus skin.",
            source_id="source_a",
        ),
        ClaimCandidate(
            text="Muscles control chromatophore expansion.",
            supporting_quote="Muscles control chromatophore expansion.",
            source_id="source_b",
        ),
    ]
    monkeypatch.setattr(verify_module, "get_llm_provider", lambda: VerifiedFakeLLM(candidates))
    monkeypatch.setattr(verify_module, "get_search_provider", lambda: NullSearch())

    assert run_verify(db_session, job) is False
    # is_hook means "confirmed by two independent domains", so the failed candidate is not flagged.
    hook_claim = (
        db_session.query(Claim)
        .filter(Claim.reject_reason == "hook_requires_two_independent_domains")
        .one()
    )
    assert hook_claim.status == "rejected"
    assert hook_claim.is_hook is False
    assert job.status == JobState.DROPPED.value


def test_script_requires_known_claim_ids_and_factual_segment_references():
    script_data = ScriptOutput(
        title_working="A fact-based title",
        hook="It dives 30 meters.",
        hook_claim_ids=["claim_1"],
        segments=[
            ScriptSegment(
                text="It dives 30 meters.",
                claim_ids=["claim_1"],
                kind="factual",
                visual_suggestion="A whale swimming",
            )
        ],
        ending="A quiet ending.",
        ending_kind="framing",
        ending_claim_ids=[],
        story_structure="observation-mechanism-conclusion",
        series="Strange Worlds",
        estimated_duration_s=40,
    )
    verified_claim = Claim(
        id="claim_1",
        job_id="job_1",
        source_ids=["source_1"],
        text="It dives 30 meters.",
        supporting_quote="It dives 30 meters.",
        quote_match_score=1.0,
        support_check="supports",
        status="verified",
        is_hook=True,
    )

    assert _validate_references(script_data, {"claim_1": verified_claim}) is None
    assert "unverified claims" in _validate_references(script_data, {})


def test_factual_script_cannot_introduce_unsupported_number():
    script_data = ScriptOutput(
        title_working="A fact-based title",
        hook="It dives 30 meters.",
        hook_claim_ids=["claim_1"],
        segments=[
            ScriptSegment(
                text="It dives 30 meters.",
                claim_ids=["claim_1"],
                kind="factual",
                visual_suggestion="A whale swimming",
            )
        ],
        ending="A quiet ending.",
        ending_kind="framing",
        ending_claim_ids=[],
        story_structure="observation-mechanism-conclusion",
        series="Strange Worlds",
        estimated_duration_s=40,
    )
    verified_claim = Claim(
        id="claim_1",
        job_id="job_1",
        source_ids=["source_1"],
        text="It dives 20 meters.",
        supporting_quote="It dives 20 meters.",
        quote_match_score=1.0,
        support_check="supports",
        status="verified",
        is_hook=True,
    )

    assert "number or unit" in _validate_references(script_data, {"claim_1": verified_claim})


class ValidatingFakeLLM:
    def __init__(self, script_data):
        self.script_data = script_data

    def generate_structured(self, prompt, schema, model_name):
        if schema is ScriptOutput:
            return self.script_data
        if schema is ScriptClaimCheck:
            return ScriptClaimCheck(status="consistent", reason="Supported")
        raise AssertionError(f"Unexpected schema requested: {schema.__name__}")


def test_unsupported_numbers_are_listed_for_the_rewrite_feedback():
    from shorts_engine.pipeline.claim_validation import unsupported_numeric_facts

    claim = "Speeds of 12-23 m/s in water."

    assert unsupported_numeric_facts("It hits 40 meters per second.", claim) == ["40 m"]
    assert unsupported_numeric_facts("It hits 23 m/s.", claim) == []
    assert unsupported_numeric_facts("Two billion dollars.", "Nothing.") == ["1000000000", "2"]


def test_claim_overlap_hints_at_facts_another_source_states():
    from shorts_engine.pipeline.claim_validation import claim_overlap

    claim = "Martian dust contains iron oxide."

    assert claim_overlap(claim, "Scientists say the dust on Mars contains iron oxide, like rust.")
    assert not claim_overlap(claim, "Venus rotates backwards compared with most planets.")
    assert not claim_overlap("", "anything")
