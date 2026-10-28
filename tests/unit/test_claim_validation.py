import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, Job, JobEvent, Script, Source, Topic
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
    monkeypatch.setattr(verify_module, "get_search_provider", lambda: object())

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
    monkeypatch.setattr(verify_module, "get_search_provider", lambda: object())

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
        story_structure="observation-mechanism-conclusion",
        series="Strange Creatures",
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
        story_structure="observation-mechanism-conclusion",
        series="Strange Creatures",
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


def test_valid_script_is_audited_before_job_becomes_recordable(db_session, monkeypatch):
    import shorts_engine.pipeline.script as script_module

    job = make_job(db_session)
    script_data = ScriptOutput(
        title_working="How octopuses change color",
        hook="Octopuses can change color quickly.",
        hook_claim_ids=["claim_1"],
        segments=[
            ScriptSegment(
                text="Octopuses can change color quickly.",
                claim_ids=["claim_1"],
                kind="factual",
                visual_suggestion="An octopus changing color",
            ),
            ScriptSegment(
                text="The change is easy to miss.",
                claim_ids=[],
                kind="framing",
                visual_suggestion="An octopus in the ocean",
            ),
        ],
        ending="A small shift can change the whole scene.",
        story_structure="observation-mechanism-conclusion",
        series="Strange Creatures",
        estimated_duration_s=40,
    )
    for index, claim_text in enumerate(
        [
            "Octopuses can change color quickly.",
            "Chromatophores are pigment cells.",
            "Muscles control chromatophore expansion.",
        ],
        start=1,
    ):
        db_session.add(
            Claim(
                id=f"claim_{index}",
                job_id=job.id,
                source_ids=[f"source_{index}"],
                text=claim_text,
                supporting_quote=claim_text,
                quote_match_score=1.0,
                support_check="supports",
                status="verified",
                is_hook=index == 1,
            )
        )
    db_session.commit()
    monkeypatch.setattr(
        script_module,
        "get_llm_provider",
        lambda: ValidatingFakeLLM(script_data),
    )

    assert run_script(db_session, job) is True
    assert job.status == JobState.AWAITING_VOICE.value
    stored_script = db_session.query(Script).filter(Script.job_id == job.id).one()
    assert stored_script.check_results["hook_claim_ids"] == ["claim_1"]
    assert len(stored_script.check_results["script_claim_checks"]) == 5
