import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Topic
from shorts_engine.domain.schemas import (
    TopicCandidate,
    TopicList,
    TopicPolicyCheck,
    TopicPolicyVerdict,
)
from shorts_engine.pipeline import topics as topics_module
from shorts_engine.pipeline.topics import (
    TopicGenerationError,
    build_generation_prompt,
    content_tokens,
    find_duplicate,
    refill_topic_queue,
)
from shorts_engine.providers.footage.base import BaseFootageProvider, FootageCandidate
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError, LLMUsage


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def candidate(text, category="animals", series="Strange Creatures", visual=8, surprise=6, **kw):
    return TopicCandidate(
        text=text,
        category=category,
        series=series,
        hook_idea=f"hook for {text}",
        subject=kw.pop("subject", " ".join(text.split()[-2:])),
        specificity_score=kw.pop("specificity", 8),
        novelty_score=kw.pop("novelty", 7),
        visual_potential_score=visual,
        hook_surprise_score=surprise,
        footage_queries=kw.pop("footage_queries", ["query a", "query b"]),
    )


class FakeLLM(BaseLLMProvider):
    """Scripted LLM: returns the given candidates, and policy verdicts by candidate text."""

    def __init__(self, batches, violations=None, fail_policy=False, omit_verdicts=()):
        self.batches = list(batches)
        self.violations = violations or {}
        self.fail_policy = fail_policy
        self.omit_verdicts = set(omit_verdicts)
        self.prompts = []

    def generate_structured(self, prompt, schema, model_name, effort=None):
        self.prompts.append(prompt)
        if self.usage_listener is not None:  # a real provider reports every billed call
            self.usage_listener(LLMUsage("fake", model_name, schema.__name__, 1000, 500))
        if schema is TopicList:
            return TopicList(topics=self.batches.pop(0) if self.batches else [])
        if schema is TopicPolicyCheck:
            if self.fail_policy:
                raise LLMProviderError("fake", "policy check unavailable")
            lines = [ln for ln in prompt.splitlines() if ln.startswith("[")]
            verdicts = []
            for line in lines:
                index = int(line[1 : line.index("]")])
                text = line[line.index("]") + 2 :]
                if text in self.omit_verdicts:
                    continue
                verdicts.append(
                    TopicPolicyVerdict(
                        index=index,
                        violated_categories=self.violations.get(text, []),
                        reason="scripted",
                    )
                )
            return TopicPolicyCheck(verdicts=verdicts)
        raise AssertionError(f"unexpected schema {schema}")


class FakeFootage(BaseFootageProvider):
    def __init__(self, clips_per_query=3, duration=10.0, height=1080, error=None):
        self.clips_per_query = clips_per_query
        self.duration = duration
        self.height = height
        self.error = error
        self.queries = []

    def search_videos(self, query, per_page=1):
        self.queries.append(query)
        if self.error:
            raise self.error
        return [
            FootageCandidate(
                provider="fake",
                asset_id=f"{query}-{i}",
                page_url="https://example.invalid",
                download_url="x.mp4",
                width=1920,
                height=self.height,
                duration_s=self.duration,
                attribution="fake",
            )
            for i in range(self.clips_per_query)
        ]


def refill(db, llm, footage, target=1):
    return refill_topic_queue(db, target=target, llm=llm, footage=footage, config=get_config())


def test_accepted_topic_is_queued_with_score_and_footage_evidence(db_session):
    report = refill(
        db_session, FakeLLM([[candidate("How owls fly silently")]]), FakeFootage(clips_per_query=3)
    )

    [topic] = report.queued
    assert topic.status == "queued"
    assert topic.score >= 7
    assert topic.footage_check["usable_clips"] == 6  # 2 queries x 3 distinct clips
    assert topic.footage_check["provider"] == "fake"
    assert report.rejected == []


def test_policy_violations_are_rejected_and_recorded(db_session):
    llm = FakeLLM(
        [[candidate("How owls fly silently"), candidate("Does this herb cure colds")]],
        violations={"Does this herb cure colds": ["medical_advice"]},
    )

    report = refill(db_session, llm, FakeFootage())

    assert [t.text for t in report.queued] == ["How owls fly silently"]
    assert ("Does this herb cure colds", "policy:medical_advice") in report.rejected
    stored = db_session.query(Topic).filter_by(text="Does this herb cure colds").one()
    assert stored.status == "rejected" and stored.reason == "policy:medical_advice"


def test_policy_check_failure_fails_closed_and_queues_nothing(db_session):
    llm = FakeLLM([[candidate("How owls fly silently")]], fail_policy=True)

    with pytest.raises(TopicGenerationError, match="policy check unavailable"):
        refill(db_session, llm, FakeFootage())

    assert db_session.query(Topic).count() == 0


def test_candidate_without_a_policy_verdict_is_rejected_not_queued(db_session):
    llm = FakeLLM([[candidate("How owls fly silently")]], omit_verdicts=["How owls fly silently"])

    report = refill(db_session, llm, FakeFootage())

    assert report.queued == []
    assert report.rejected == [("How owls fly silently", "policy_unchecked")]


def test_reworded_duplicate_of_existing_topic_is_rejected(db_session):
    db_session.add(Topic(id="old", text="How a dung beetle rolls a ball", category="animals"))
    db_session.commit()
    llm = FakeLLM(
        [
            [
                candidate("Why dung beetles roll balls", subject="dung beetle"),
                candidate("How owls fly silently"),
            ]
        ]
    )

    report = refill(db_session, llm, FakeFootage())

    assert [t.text for t in report.queued] == ["How owls fly silently"]
    assert report.rejected[0][0] == "Why dung beetles roll balls"
    assert report.rejected[0][1].startswith("duplicate_of:")


def test_duplicates_within_one_batch_are_rejected(db_session):
    llm = FakeLLM([[candidate("How owls fly silently"), candidate("Why owls fly silently")]])

    report = refill(db_session, llm, FakeFootage(), target=2)

    assert len(report.queued) == 1


def test_topic_with_too_little_footage_is_rejected(db_session):
    report = refill(
        db_session,
        FakeLLM([[candidate("How owls fly silently")]] * 3),
        FakeFootage(clips_per_query=1),
    )

    assert report.queued == []
    assert report.rejected[0][1] == "insufficient_footage:2"


@pytest.mark.parametrize("kwargs", [{"duration": 2.0}, {"height": 480}])
def test_short_or_low_resolution_clips_do_not_count(db_session, kwargs):
    report = refill(
        db_session, FakeLLM([[candidate("How owls fly silently")]] * 3), FakeFootage(**kwargs)
    )

    assert report.queued == []
    assert report.rejected[0][1] == "insufficient_footage:0"


def test_unknown_category_or_series_is_rejected_without_calling_footage(db_session):
    llm = FakeLLM(
        [
            [
                candidate("How owls fly silently", category="astrology"),
                candidate("Why ants carry loads", series="Made Up Series"),
            ]
        ]
        * 3
    )
    footage = FakeFootage()

    report = refill(db_session, llm, footage)

    assert {reason for _, reason in report.rejected} == {"unknown_category", "unknown_series"}
    assert footage.queries == []


def test_footage_provider_failure_is_an_error_and_nothing_is_persisted(db_session):
    from shorts_engine.providers.footage.base import FootageProviderError

    footage = FakeFootage(error=FootageProviderError("fake", "no key"))

    with pytest.raises(TopicGenerationError, match="no key"):
        refill(db_session, FakeLLM([[candidate("How owls fly silently")]]), footage)

    assert db_session.query(Topic).count() == 0


def test_refill_stops_at_target_and_is_bounded_by_max_batches(db_session):
    # Each batch is rejected for footage, so the loop must stop after max_batches (default 3).
    llm = FakeLLM([[candidate(f"Unique subject number {n} thing")] for n in range(10)])

    report = refill(db_session, llm, FakeFootage(clips_per_query=0), target=5)

    assert report.batches == 3
    assert len(llm.batches) == 7


def test_refill_does_nothing_when_queue_is_already_full(db_session):
    db_session.add(Topic(id="q", text="Existing", category="animals", status="queued"))
    db_session.commit()
    llm = FakeLLM([])

    report = refill(db_session, llm, FakeFootage(), target=1)

    assert report.batches == 0 and llm.prompts == []


def test_generation_prompt_uses_policy_style_guide_and_known_topics():
    config = get_config()
    from collections import Counter

    prompt = build_generation_prompt(
        config, "STYLE-GUIDE-TEXT", 5, ["Known topic one"], Counter(animals=2), Counter()
    )

    assert "STYLE-GUIDE-TEXT" in prompt
    assert "Known topic one" in prompt
    assert "Propose exactly 5 new topics" in prompt
    for forbidden in config.topic_policy["forbidden_topics"]:
        assert forbidden in prompt
    for series in config.topic_policy["series"]:
        assert series["name"] in prompt
    assert "Bagaimana" not in prompt  # prompts are English


def test_duplicate_detection_ignores_wording_but_not_substance():
    assert find_duplicate("Why octopuses change color", ["How an octopus changes color"], 0.5)
    assert not find_duplicate(
        "Why octopuses have three hearts", ["How an octopus changes color"], 0.5
    )
    assert content_tokens("The Owls") == content_tokens("an owl")


def test_mock_providers_can_fill_a_queue_without_network(db_session):
    from shorts_engine.providers.footage.mock import MockFootageProvider
    from shorts_engine.providers.llm.mock import MockLLMProvider

    report = refill_topic_queue(
        db_session,
        target=4,
        llm=MockLLMProvider(),
        footage=MockFootageProvider(),
        config=get_config(),
    )

    assert len(report.queued) >= 4
    assert all(t.status == "queued" for t in report.queued)


def test_module_exposes_no_hardcoded_indonesian_prompts():
    import inspect

    source = inspect.getsource(topics_module)
    assert "Hasilkan" not in source and "Bagaimana Gurita" not in source


def test_overused_topic_is_rejected_even_when_reworded(db_session):
    llm = FakeLLM(
        [
            [
                candidate("Why cats seem to land on their feet", subject="house cat reflex"),
                candidate("How a mantis shrimp strikes", subject="mantis shrimp"),
            ]
        ]
    )

    report = refill(db_session, llm, FakeFootage())

    assert [t.text for t in report.queued] == ["How a mantis shrimp strikes"]
    assert report.rejected[0][1].startswith("overused:")


def test_generic_subjects_are_rejected_without_calling_footage(db_session):
    llm = FakeLLM([[candidate("Something about the ocean", subject="ocean")]] * 3)
    footage = FakeFootage()

    report = refill(db_session, llm, footage)

    assert report.rejected[0][1] == "generic_subject:ocean"
    assert footage.queries == []


def test_low_specificity_and_low_novelty_are_rejected(db_session):
    llm = FakeLLM(
        [
            [
                candidate("How a barn owl hunts", subject="barn owl", specificity=5),
                candidate("How a pistol shrimp snaps", subject="pistol shrimp", novelty=3),
            ]
        ]
        * 3
    )

    report = refill(db_session, llm, FakeFootage())

    reasons = {reason for _, reason in report.rejected}
    assert {"low_specificity:5", "low_novelty:3"} <= reasons


def test_two_topics_about_the_same_subject_in_one_batch_keep_only_the_first(db_session):
    llm = FakeLLM(
        [
            [
                candidate("How a mantis shrimp strikes", subject="mantis shrimp"),
                candidate("What a mantis shrimp sees", subject="Mantis Shrimp"),
            ]
        ]
    )

    report = refill(db_session, llm, FakeFootage(), target=2)

    assert [t.text for t in report.queued] == ["How a mantis shrimp strikes"]
    assert any(reason.startswith("duplicate_subject:") for _, reason in report.rejected)


def test_prompt_demands_specific_subjects_and_lists_overused_topics():
    from collections import Counter

    prompt = build_generation_prompt(get_config(), "STYLE", 4, [], Counter(), Counter())

    assert "Name a particular subject" in prompt
    assert "cats landing on their feet" in prompt  # overused list is shown to the model
    assert "ocean" in prompt  # generic subjects are listed
    assert "name the specific subject itself" in prompt


def test_stemming_matches_inflections():
    assert content_tokens("cats landing on their feet") >= content_tokens("cat lands")
    assert content_tokens("Octopuses changes colors") == content_tokens("octopus change color")


def test_more_specific_and_novel_topics_score_higher():
    from collections import Counter

    from shorts_engine.pipeline.topics import score_topic

    plain = candidate("Plain topic", specificity=7, novelty=6)
    sharp = candidate("Sharp topic", specificity=10, novelty=9)

    plain_score, _ = score_topic(plain, Counter(), Counter())
    sharp_score, reason = score_topic(sharp, Counter(), Counter())

    assert sharp_score > plain_score
    assert "specificity 10" in reason


def test_refill_records_the_cost_of_each_llm_call(db_session):
    from shorts_engine.db.models import CostEntry

    refill(db_session, FakeLLM([[candidate("How owls fly silently")]]), FakeFootage())

    operations = [e.operation for e in db_session.query(CostEntry).order_by(CostEntry.id)]
    assert operations == ["TopicList", "TopicPolicyCheck"]
    assert all(e.job_id is None for e in db_session.query(CostEntry))  # topic costs have no job


def test_cost_is_kept_even_when_a_batch_fails_after_the_llm_calls(db_session):
    from shorts_engine.db.models import CostEntry
    from shorts_engine.providers.footage.base import FootageProviderError

    footage = FakeFootage(error=FootageProviderError("fake", "down"))

    with pytest.raises(TopicGenerationError):
        refill(db_session, FakeLLM([[candidate("How owls fly silently")]]), footage)

    assert db_session.query(CostEntry).count() == 2  # money was spent, so it stays recorded
    assert db_session.query(Topic).count() == 0  # but the half-finished batch is rolled back


def test_refill_is_refused_before_any_llm_call_when_the_budget_is_spent(db_session):
    from shorts_engine.db.models import CostEntry
    from shorts_engine.services.costs import BudgetExceeded

    db_session.add(CostEntry(provider="anthropic", operation="x", cost_usd=100.0))
    db_session.commit()
    llm = FakeLLM([[candidate("How owls fly silently")]])

    with pytest.raises(BudgetExceeded):
        refill(db_session, llm, FakeFootage())

    assert llm.prompts == []


def test_refill_restores_the_previous_usage_listener(db_session):
    llm = FakeLLM([[candidate("How owls fly silently")]])
    sentinel = lambda usage: None  # noqa: E731
    llm.usage_listener = sentinel

    refill(db_session, llm, FakeFootage())

    assert llm.usage_listener is sentinel
