import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shorts_engine.config import get_config
from shorts_engine.db.base import Base
from shorts_engine.db.models import Claim, CostEntry, Job, JobEvent, Script, Topic
from shorts_engine.domain.schemas import ScriptClaimCheck, ScriptOutput, ScriptSegment
from shorts_engine.domain.state_machine import JobState
from shorts_engine.pipeline.script import (
    build_script_prompt,
    next_episode_number,
    reference_problems,
    run_script,
    script_problems,
)
from shorts_engine.pipeline.script_rules import (
    count_second_person,
    count_words,
    find_forbidden_phrases,
    find_unclaimed_overstatements,
    find_unsupported_names,
    spoken_text,
    word_budget,
)
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError, LLMUsage
from shorts_engine.services.costs import BudgetExceeded

CLAIM_TEXTS = [
    "Mantis shrimp strike with club-like limbs at very high speed.",
    "The strike can create tiny bubbles in the water.",
    "Those bubbles collapse and release a burst of energy.",
    "The animals live in shallow tropical seas.",
]


def valid_draft(**overrides):
    segments = [
        ScriptSegment(
            text=(
                "The shrimp swings its club-like limbs outward and strikes at very high speed, "
                "faster than the eye can follow."
            ),
            claim_ids=["c1"],
            kind="factual",
            visual_suggestion="a mantis shrimp near a reef",
        ),
        ScriptSegment(
            text="That speed can create tiny bubbles in the water right in front of the moving limb.",
            claim_ids=["c2"],
            kind="factual",
            visual_suggestion="bubbles rising underwater",
        ),
        ScriptSegment(
            text="When those bubbles collapse, they release a burst of energy that adds to the blow.",
            claim_ids=["c3"],
            kind="factual",
            visual_suggestion="a calm reef scene",
        ),
        ScriptSegment(
            text=(
                "These small hunters live in shallow tropical seas, where they hide in burrows "
                "between every strike."
            ),
            claim_ids=["c4"],
            kind="factual",
            visual_suggestion="shallow tropical water",
        ),
        ScriptSegment(
            text="It is a quiet reminder that speed can reshape the water around a tiny animal.",
            kind="framing",
            visual_suggestion="sunlight on the sea",
        ),
    ]
    data = {
        "title_working": "How a mantis shrimp strikes",
        "hook": "A mantis shrimp can strike with club-like limbs at very high speed, quietly and fast.",
        "hook_claim_ids": ["c1"],
        "segments": segments,
        "ending": "Something so small can leave a very large mark on the water.",
        "ending_kind": "framing",
        "estimated_duration_s": 999,  # the model's own guess must not be trusted
        "story_structure": "observation -> mechanism -> conclusion",
        "series": "Strange Creatures",
    }
    data.update(overrides)
    return ScriptOutput(**data)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_job(db, job_id="job_1", hook_eligible=True):
    topic_id = f"topic_{job_id}"
    db.add(Topic(id=topic_id, text="How a mantis shrimp strikes", category="ocean"))
    job = Job(id=job_id, topic_id=topic_id, status=JobState.VERIFYING.value)
    db.add(job)
    for index, text in enumerate(CLAIM_TEXTS, start=1):
        db.add(
            Claim(
                id=f"c{index}" if job_id == "job_1" else f"{job_id}_c{index}",
                job_id=job_id,
                source_ids=["s"],
                text=text,
                supporting_quote=text,
                quote_match_score=1.0,
                support_check="supports",
                status="verified",
                is_hook=hook_eligible and index == 1,
            )
        )
    db.commit()
    return job


class ScriptLLM(BaseLLMProvider):
    def __init__(self, drafts, flagged=None, error=None, bill_per_draft=False):
        self.drafts = list(drafts)
        self.flagged = flagged or {}  # statement text -> verdict from the model check
        self.error = error
        self.bill_per_draft = bill_per_draft
        self.prompts: list[str] = []
        self.check_calls = 0

    def generate_structured(self, prompt, schema, model_name, effort=None):
        if schema is ScriptOutput:
            self.prompts.append(prompt)
            if self.error:
                raise self.error
            if self.bill_per_draft and self.usage_listener:
                self.usage_listener(
                    LLMUsage("fake", "claude-sonnet-5-5", "ScriptOutput", 0, 10_000_000)
                )
            return self.drafts.pop(0)
        assert schema is ScriptClaimCheck
        self.check_calls += 1
        statement = prompt.split("Statement: ", 1)[1].split("\nVerified claims:", 1)[0]
        verdict = self.flagged.get(statement, "consistent")
        return ScriptClaimCheck(status=verdict, reason="scripted")


def claims_of(db, job_id="job_1"):
    return {c.id: c for c in db.query(Claim).filter(Claim.job_id == job_id)}


def stage(db, job, llm):
    return run_script(db, job, llm=llm, config=get_config())


# ------------------------------------------------------------------ the fixture itself


def test_the_valid_fixture_really_is_valid(db_session):
    make_job(db_session)

    assert script_problems(valid_draft(), claims_of(db_session), get_config()) == []


# ------------------------------------------------------------------ deterministic rules


def test_pronunciation_hints_are_not_spoken_words():
    assert spoken_text("A cephalopod [SEF-uh-luh-pod] swims.") == "A cephalopod swims."
    assert count_words("A cephalopod [SEF-uh-luh-pod] swims.") == 3


def test_word_budget_follows_duration_and_speech_rate():
    assert word_budget(35, 55, 2.6) == (91, 143)  # the style guide says about 90-140 words


def test_forbidden_openers_and_calls_to_action_are_found():
    phrases = ["did you know", "like and subscribe"]

    assert find_forbidden_phrases("Did you know owls fly silently?", phrases) == ["did you know"]
    assert find_forbidden_phrases("Owls fly silently.", phrases) == []


def test_strong_words_need_a_claim_that_states_them():
    words = ["the only", "always", "fastest"]

    assert find_unclaimed_overstatements("It is the only one.", "It is rare.", words) == [
        "the only"
    ]
    assert find_unclaimed_overstatements("It is the only one.", "It is the only one.", words) == []
    assert find_unclaimed_overstatements("It almost always wins.", "It wins.", words) == ["always"]


def test_names_must_appear_in_the_cited_claims():
    claims = "Mantis shrimp live in the Pacific."

    assert find_unsupported_names("The Pacific is home to them.", claims) == []
    assert find_unsupported_names("They also live near Hawaii.", claims) == ["Hawaii"]
    assert find_unsupported_names("Hawaii is first so it is ignored.", claims) == []


def test_second_person_is_counted():
    assert count_second_person("You may see it. Your eyes help.") == 2


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"series": "Made Up Series"}, "The series must be exactly one of"),
        ({"ending": "Like and subscribe for more."}, 'forbidden phrase "like and subscribe"'),
        ({"hook": "A mantis shrimp strikes at the fastest speed ever."}, 'says "fastest"'),
        ({"ending": "It all happens far from Hawaii, on a quiet reef."}, 'names "Hawaii"'),
        ({"hook_claim_ids": ["c2"]}, "confirmed by two independent sources"),
        ({"segments": valid_draft().segments[:2]}, "body segments"),
    ],
)
def test_each_rule_produces_an_actionable_problem(db_session, override, expected):
    make_job(db_session)

    problems = script_problems(valid_draft(**override), claims_of(db_session), get_config())

    assert any(expected in problem for problem in problems), problems


def test_too_short_scripts_are_rejected_with_the_word_range(db_session):
    make_job(db_session)
    short = valid_draft(segments=valid_draft().segments[:3], ending="Short.")

    problems = script_problems(short, claims_of(db_session), get_config())

    assert any("between 91 and 143 words" in p for p in problems)


# ------------------------------------------------------------------ the prompt


def test_prompt_is_english_and_carries_the_style_guide_series_and_limits(db_session):
    make_job(db_session)
    claims = list(claims_of(db_session).values())

    prompt = build_script_prompt(get_config(), "How a mantis shrimp strikes", claims, "STYLE-TEXT")

    assert "STYLE-TEXT" in prompt
    assert 'language with code "en"' in prompt
    assert "Strange Creatures" in prompt and "observation -> mechanism -> conclusion" in prompt
    assert "91 to 143 words" in prompt
    assert prompt.count("HOOK-ELIGIBLE") >= 2  # one claim is marked, plus the rule text
    assert "[c1] Mantis shrimp strike" in prompt
    assert "Tulis" not in prompt and "naskah" not in prompt


def test_prompt_includes_rejection_feedback_only_on_rewrites(db_session):
    make_job(db_session)
    claims = list(claims_of(db_session).values())

    first = build_script_prompt(get_config(), "t", claims, "S")
    retry = build_script_prompt(get_config(), "t", claims, "S", ["The script has 50 words."])

    assert "rejected" not in first
    assert "previous draft was rejected" in retry and "The script has 50 words." in retry


def test_the_real_style_guide_file_is_what_the_stage_reads(db_session):
    make_job(db_session)
    llm = ScriptLLM([valid_draft()])

    stage(db_session, db_session.query(Job).one(), llm)

    assert "Trust is the brand" in llm.prompts[0]  # a line from config/style_guide.md


# ------------------------------------------------------------------ the stage


def test_a_clean_first_draft_becomes_a_recordable_script(db_session):
    job = make_job(db_session)
    llm = ScriptLLM([valid_draft()])

    assert stage(db_session, job, llm) is True

    assert job.status == JobState.AWAITING_VOICE.value
    script = db_session.query(Script).one()
    assert script.version == 1 and script.status == "passed"
    results = script.check_results
    assert results["estimated_duration_s"] != 999  # computed from words, not taken from the model
    assert 35 <= results["estimated_duration_s"] <= 55
    assert results["estimated_duration_s"] == round(results["word_count"] / 2.6)
    assert len(results["script_claim_checks"]) == 8  # hook, 5 segments, ending, title
    assert (job.series, job.episode_number) == ("Strange Creatures", 1)


def test_episode_numbers_count_up_within_a_series(db_session):
    first = make_job(db_session)
    second = make_job(db_session, "job_2")

    stage(db_session, first, ScriptLLM([valid_draft()]))
    claims = claims_of(db_session, "job_2")
    draft = valid_draft(
        hook_claim_ids=["job_2_c1"],
        segments=[
            s.model_copy(update={"claim_ids": [f"job_2_{cid}" for cid in s.claim_ids]})
            for s in valid_draft().segments
        ],
    )
    assert script_problems(draft, claims, get_config()) == []
    stage(db_session, second, ScriptLLM([draft]))

    assert (first.episode_number, second.episode_number) == (1, 2)
    assert next_episode_number(db_session, "Strange Creatures") == 3
    assert next_episode_number(db_session, "Science at Home") == 1


def test_a_bad_draft_is_rewritten_with_feedback_and_the_version_records_it(db_session):
    job = make_job(db_session)
    bad = valid_draft(ending="Like and subscribe for more.")
    llm = ScriptLLM([bad, valid_draft()])

    assert stage(db_session, job, llm) is True

    assert len(llm.prompts) == 2
    assert "previous draft was rejected" in llm.prompts[1]
    assert 'forbidden phrase "like and subscribe"' in llm.prompts[1]
    assert db_session.query(Script).one().version == 2
    path = [e.to_state for e in db_session.query(JobEvent).order_by(JobEvent.id)]
    assert path == [
        "scripting",
        "script_check",
        "scripting",
        "script_check",
        "awaiting_voice",
    ]


def test_a_model_check_failure_triggers_a_rewrite_too(db_session):
    job = make_job(db_session)
    flagged_text = valid_draft().segments[1].text
    llm = ScriptLLM([valid_draft()] * 3, flagged={flagged_text: "overstated"})

    # The same flagged text appears in the rewrite as well, so every attempt fails.
    assert stage(db_session, job, llm) is False

    assert job.status == JobState.DROPPED.value
    assert len(llm.prompts) == 3
    assert "overstated by its claims" in llm.prompts[1]


def test_three_failures_drop_the_topic_instead_of_failing_the_job(db_session):
    job = make_job(db_session)
    bad = valid_draft(ending="Like and subscribe for more.")
    llm = ScriptLLM([bad, bad, bad])

    assert stage(db_session, job, llm) is False

    assert job.status == JobState.DROPPED.value  # not FAILED: the topic could not be scripted
    assert len(llm.prompts) == 3  # one draft and at most two rewrites
    assert llm.check_calls == 0  # deterministic rules failed first, so no model check was paid
    assert db_session.query(Script).count() == 0
    event = db_session.query(JobEvent).order_by(JobEvent.id.desc()).first()
    assert event.reason.startswith("script_check_failed:")


def test_model_checks_run_only_on_drafts_that_passed_the_free_rules(db_session):
    job = make_job(db_session)
    llm = ScriptLLM([valid_draft(ending="Like and subscribe for more."), valid_draft()])

    stage(db_session, job, llm)

    assert llm.check_calls == 8  # only the clean second draft was checked


def test_a_provider_error_fails_the_job_without_rewriting(db_session):
    job = make_job(db_session)
    llm = ScriptLLM([], error=LLMProviderError("fake", "refused"))

    assert stage(db_session, job, llm) is False

    assert job.status == JobState.FAILED.value
    assert len(llm.prompts) == 1


def test_no_hook_eligible_claim_drops_the_topic_before_any_paid_call(db_session):
    job = make_job(db_session, hook_eligible=False)
    llm = ScriptLLM([valid_draft()])

    assert stage(db_session, job, llm) is False

    assert job.status == JobState.DROPPED.value
    assert llm.prompts == []


def test_a_rewrite_is_refused_when_the_budget_runs_out_mid_stage(db_session):
    job = make_job(db_session)
    bad = valid_draft(ending="Like and subscribe for more.")
    llm = ScriptLLM([bad, valid_draft()], bill_per_draft=True)

    with pytest.raises(BudgetExceeded):
        stage(db_session, job, llm)

    assert len(llm.prompts) == 1  # the second paid draft was never requested
    assert db_session.query(CostEntry).filter_by(job_id="job_1").count() == 1


# ------------------------------------------------------------------ kind can no longer default


def test_a_segment_must_say_whether_it_is_factual_or_framing():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScriptSegment(text="An opener.", visual_suggestion="a reef")  # no kind: refused

    schema = ScriptOutput.model_json_schema()
    assert "ending_kind" in schema["required"]
    assert "kind" in schema["$defs"]["ScriptSegment"]["required"]


# ------------------------------------------------------------------ feedback the model can act on


def numeric_claims():
    common = dict(
        job_id="j",
        source_ids=["s"],
        quote_match_score=1.0,
        support_check="supports",
        status="verified",
        supporting_quote="q",
    )
    return {
        "c1": Claim(
            id="c1",
            is_hook=True,
            text="High-speed imaging showed speeds of 12-23 m/s in water.",
            **common,
        ),
        "c2": Claim(
            id="c2",
            is_hook=False,
            text="Mantis shrimp strikes deliver peak forces of 1500 N.",
            **common,
        ),
    }


def seg(text, claim_ids, kind):
    return ScriptSegment(text=text, claim_ids=claim_ids, kind=kind, visual_suggestion="a reef")


def numeric_draft(**overrides):
    data = valid_draft().model_dump()
    data["hook_claim_ids"] = ["c1"]
    data["segments"] = [
        seg("The club reaches 23 meters per second in water.", ["c1"], "factual"),
        seg("It is one of the fastest strikes known.", [], "framing"),
        seg("The blow lands with 1,500 newtons of force.", ["c2"], "factual"),
    ]
    data.update(overrides)
    return ScriptOutput(**data)


def test_natural_paraphrases_of_a_claim_no_longer_count_as_errors():
    draft = numeric_draft(hook="A mantis shrimp strikes at 12 to 23 meters per second.")

    # "meters per second" for m/s, "one of the fastest" as a phrase, "newtons" for N
    assert reference_problems(draft, numeric_claims()) == []


def test_every_problem_is_listed_and_names_the_sentence_and_the_numbers():
    draft = numeric_draft(
        segments=[
            seg("It moves 40 meters per second.", ["c1"], "factual"),
            seg("A fact with no source.", [], "factual"),
            seg("A transition.", ["c2"], "framing"),
        ],
        hook_claim_ids=["c2"],
        ending_claim_ids=["c9"],
    )

    problems = reference_problems(draft, numeric_claims())
    text = "\n".join(problems)

    assert len(problems) >= 5  # all of them, not just the first
    assert "40 m" in text and "It moves 40 meters per second." in text
    assert "Its cited claims say" in text and "12-23 m/s" in text
    assert 'Segment 2 is marked factual but cites no claim: "A fact with no source."' in text
    assert "Segment 3 is marked framing but cites c2" in text
    assert "c2 is not one" in text and "eligible: c1" in text
    assert "c9" in text and "Cite only these claim ids: c1, c2" in text


def test_a_framing_line_that_states_a_number_is_pointed_out():
    draft = numeric_draft(segments=[seg("It happens in 3 seconds.", [], "framing")])

    problems = reference_problems(draft, numeric_claims())

    assert any("3 s" in p and "segment 1" in p for p in problems)


# ------------------------------------------------------------------ every draft is kept


def read_attempts(job_id="job_1"):
    import json

    from shorts_engine.db.base import get_data_dir

    path = get_data_dir() / "jobs" / job_id / "script" / "attempts.json"
    return json.loads(path.read_text(encoding="utf-8"))["attempts"]


def test_rejected_and_accepted_drafts_are_kept_with_their_problems(db_session):
    job = make_job(db_session)
    bad = valid_draft(ending="Like and subscribe for more.")
    stage(db_session, job, ScriptLLM([bad, valid_draft()]))

    attempts = read_attempts()

    assert [a["outcome"] for a in attempts] == ["rejected", "passed"]
    assert any("like and subscribe" in p for p in attempts[0]["problems"])
    assert attempts[0]["draft"]["ending"] == "Like and subscribe for more."
    assert attempts[1]["problems"] == [] and len(attempts[1]["model_checks"]) == 8


def test_a_topic_dropped_after_three_drafts_still_has_all_of_them_on_disk(db_session):
    job = make_job(db_session)
    bad = valid_draft(ending="Like and subscribe for more.")
    stage(db_session, job, ScriptLLM([bad, bad, bad]))

    attempts = read_attempts()

    assert [a["attempt"] for a in attempts] == [1, 2, 3]
    assert all(a["outcome"] == "rejected" and a["problems"] for a in attempts)
