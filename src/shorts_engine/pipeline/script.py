import json
import logging
import uuid

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config, load_style_guide
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Claim, Job, Script, Topic
from shorts_engine.domain.schemas import ScriptClaimCheck, ScriptOutput
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.claim_validation import unsupported_numeric_facts
from shorts_engine.pipeline.script_rules import (
    count_second_person,
    count_words,
    describe,
    find_forbidden_phrases,
    find_unclaimed_overstatements,
    find_unsupported_names,
    length_problem,
    word_budget,
)
from shorts_engine.providers.factory import get_llm_provider, is_mock_provider
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError
from shorts_engine.services.costs import check_budget, track_costs

logger = logging.getLogger(__name__)

HOOK_MARK = "[HOOK-ELIGIBLE: confirmed by two independent sources]"


def _claim_text_for_ids(claim_ids: list[str], claims_by_id: dict[str, Claim]) -> str:
    return "\n".join(f"[{claim_id}] {claims_by_id[claim_id].text}" for claim_id in claim_ids)


def reference_problems(script_data: ScriptOutput, claims_by_id: dict[str, Claim]) -> list[str]:
    """Every claim-reference problem of a draft, each naming the sentence and what to change.

    The text goes back to the model as feedback for the rewrite, so it says which segment, quotes
    the sentence, lists the offending numbers and, where useful, what the cited claims actually say.
    """
    problems: list[str] = []
    known = ", ".join(sorted(claims_by_id))
    eligible = ", ".join(sorted(i for i, c in claims_by_id.items() if c.is_hook)) or "none"

    cited = set(script_data.hook_claim_ids) | set(script_data.ending_claim_ids)
    for segment in script_data.segments:
        cited |= set(segment.claim_ids)
    unknown = cited - claims_by_id.keys()
    if unknown:
        problems.append(
            f"Script references unverified claims: {', '.join(sorted(unknown))}. "
            f"Cite only these claim ids: {known}."
        )

    def stated(claim_ids: list[str]) -> str:
        return _claim_text_for_ids([i for i in claim_ids if i in claims_by_id], claims_by_id)

    def numbers_problem(label: str, text: str, claim_ids: list[str]) -> str | None:
        reference = stated(claim_ids)
        missing = unsupported_numeric_facts(text, reference)
        if not missing:
            return None
        said = f' Its cited claims say: "{reference.replace(chr(10), " | ")}".' if reference else ""
        return (
            f"The {label} contains a number or unit ({', '.join(missing)}) that its cited claims "
            f'do not state: "{text}".{said} Use only numbers and units from the claims.'
        )

    if not script_data.hook_claim_ids:
        problems.append(
            "Hook has no verified claim references. Cite a HOOK-ELIGIBLE claim in "
            f"hook_claim_ids (eligible: {eligible})."
        )
    else:
        not_eligible = [
            i
            for i in script_data.hook_claim_ids
            if i in claims_by_id and not claims_by_id[i].is_hook
        ]
        if not_eligible:
            problems.append(
                "Hook must rest on a claim confirmed by two independent sources; "
                f"{', '.join(not_eligible)} is not one. Use only HOOK-ELIGIBLE claims "
                f"(eligible: {eligible})."
            )
        found = numbers_problem("hook", script_data.hook, script_data.hook_claim_ids)
        if found:
            problems.append(found)

    for index, segment in enumerate(script_data.segments, start=1):
        label = f"segment {index}"
        if segment.kind == "factual" and not segment.claim_ids:
            problems.append(
                f'Segment {index} is marked factual but cites no claim: "{segment.text}". '
                "Cite the claim it states, or mark it framing and remove every fact from it."
            )
        if segment.kind == "framing" and segment.claim_ids:
            problems.append(
                f"Segment {index} is marked framing but cites {', '.join(segment.claim_ids)}. "
                "Framing lines state no facts: remove the claim ids, or mark it factual."
            )
        found = numbers_problem(label, segment.text, segment.claim_ids)
        if found:
            problems.append(found)

    if script_data.ending_kind == "factual" and not script_data.ending_claim_ids:
        problems.append(
            f'The ending is marked factual but cites no claim: "{script_data.ending}". '
            "Cite its claim or mark it framing."
        )
    if script_data.ending_kind == "framing" and script_data.ending_claim_ids:
        problems.append(
            "The ending is marked framing but cites claims. Remove the ids or mark it factual."
        )
    found = numbers_problem("ending", script_data.ending, script_data.ending_claim_ids)
    if found:
        problems.append(found)
    return problems


def _validate_references(script_data: ScriptOutput, claims_by_id: dict[str, Claim]):
    """The first reference problem, or None. Kept for callers that need a single verdict."""
    problems = reference_problems(script_data, claims_by_id)
    return problems[0] if problems else None


def _statements(script_data: ScriptOutput, claims_by_id: dict[str, Claim]):
    """Every spoken or published statement with the claim ids that must back it."""
    statements = [("hook", script_data.hook, script_data.hook_claim_ids)]
    for index, segment in enumerate(script_data.segments, start=1):
        statements.append((f"segment {index}", segment.text, segment.claim_ids))
    statements.append(("ending", script_data.ending, script_data.ending_claim_ids))
    statements.append(("title", script_data.title_working, list(claims_by_id)))
    return statements


def _spoken_parts(script_data: ScriptOutput) -> list[str]:
    return [script_data.hook, *[s.text for s in script_data.segments], script_data.ending]


def build_script_prompt(
    config: AppConfig,
    topic_text: str,
    claims: list[Claim],
    style_guide: str,
    feedback: list[str] | None = None,
) -> str:
    speech = config.settings["speech"]
    script_cfg = config.settings.get("script", {})
    low, high = word_budget(
        speech["target_duration_min"], speech["target_duration_max"], speech["words_per_second"]
    )
    series_lines = "\n".join(
        f"- {item['name']}: story structure {item['structure']}; "
        f'hook pattern "{item["hook_pattern"]}"'
        for item in config.topic_policy["series"]
    )
    claim_lines = "\n".join(
        f"- [{claim.id}] {claim.text}" + (f" {HOOK_MARK}" if claim.is_hook else "")
        for claim in claims
    )
    language = config.settings["content_language"]
    min_segments = int(script_cfg.get("min_body_segments", 3))
    max_segments = int(script_cfg.get("max_body_segments", 5))
    retry = ""
    if feedback:
        retry = (
            "\nYour previous draft was rejected. Write a new draft that fixes every point:\n"
            f"{describe(feedback)}\n"
        )
    return f"""You write the narration for a short educational science video that a person will read
aloud. Write all spoken text in the language with code "{language}".

Follow this style guide exactly:
---
{style_guide}
---

Choose the series that fits the topic and follow its story structure:
{series_lines}

Topic: {topic_text}

Verified facts. These are the ONLY facts you may state:
{claim_lines}

Rules:
- Every factual sentence lists the claim_ids that back it. Openers, transitions and closing lines
  state no facts: mark them as framing and give them no claim_ids.
- hook_claim_ids may contain only claims marked HOOK-ELIGIBLE.
- Never add numbers, units, names or details that the claims do not contain, and never state
  anything stronger than a claim does. Words such as "only", "always", "never", "most" and
  superlatives are forbidden unless a claim says them.
- Length: {low} to {high} words in total across the hook, segments and ending, spoken at
  {speech["words_per_second"]} words per second. Use {min_segments} to {max_segments} body segments.
- Sentences of 8 to 14 words. Use "you" at most once in the whole script.
- Put a pronunciation hint in square brackets after hard words, for example
  cephalopod [SEF-uh-luh-pod]. Hints are not counted as spoken words.
- visual_suggestion names concrete things stock footage of the subject can show. Avoid diagrams,
  text overlays, microscope views and anatomy.
{retry}"""


def _save_attempts(job_id: str, attempts: list[dict]) -> None:
    """Keep every draft with its problems, so a rejected script can be read afterwards."""
    folder = get_data_dir() / "jobs" / job_id / "script"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "attempts.json").write_text(
        json.dumps({"attempts": attempts}, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def next_episode_number(db: Session, series: str) -> int:
    done = db.query(Job).filter(Job.series == series, Job.episode_number.isnot(None)).count()
    return done + 1


def script_problems(
    script_data: ScriptOutput, claims_by_id: dict[str, Claim], config: AppConfig
) -> list[str]:
    """All deterministic problems of a draft, worded as instructions for the next rewrite."""
    speech = config.settings["speech"]
    script_cfg = config.settings.get("script", {})
    problems: list[str] = []

    problems.extend(reference_problems(script_data, claims_by_id))

    series_names = [item["name"] for item in config.topic_policy["series"]]
    if script_data.series not in series_names:
        problems.append(f"The series must be exactly one of: {', '.join(series_names)}.")

    low_segments = int(script_cfg.get("min_body_segments", 3))
    high_segments = int(script_cfg.get("max_body_segments", 5))
    if not low_segments <= len(script_data.segments) <= high_segments:
        problems.append(
            f"Use between {low_segments} and {high_segments} body segments, "
            f"not {len(script_data.segments)}."
        )

    texts = _spoken_parts(script_data)
    total_words = sum(count_words(text) for text in texts)
    budget = word_budget(
        speech["target_duration_min"], speech["target_duration_max"], speech["words_per_second"]
    )
    size_problem = length_problem(total_words, budget, speech["words_per_second"])
    if size_problem:
        problems.append(size_problem)

    max_you = int(script_cfg.get("max_second_person_uses", 1))
    if sum(count_second_person(text) for text in texts) > max_you:
        problems.append(f'Address the viewer as "you" at most {max_you} time(s).')

    forbidden = list(script_cfg.get("forbidden_phrases", []))
    overstated = list(script_cfg.get("overstatement_words", []))
    for label, text, claim_ids in _statements(script_data, claims_by_id):
        known_ids = [claim_id for claim_id in claim_ids if claim_id in claims_by_id]
        claims_text = _claim_text_for_ids(known_ids, claims_by_id)
        for phrase in find_forbidden_phrases(text, forbidden):
            problems.append(f'The {label} uses the forbidden phrase "{phrase}".')
        for word in find_unclaimed_overstatements(text, claims_text, overstated):
            problems.append(f'The {label} says "{word}" but no cited claim states it.')
        for name in find_unsupported_names(text, claims_text):
            problems.append(f'The {label} names "{name}", which its cited claims do not mention.')
    return problems


def run_script(
    db: Session,
    job: Job,
    llm: BaseLLMProvider | None = None,
    config: AppConfig | None = None,
) -> bool:
    config = config or get_config()
    llm = llm or get_llm_provider()

    if is_mock_provider(llm):
        transition_job_state(
            db,
            job,
            JobState.DROPPED,
            reason="mock_provider_not_allowed_for_production_script",
        )
        return False

    check_budget(db, config, job.id)  # refused before any paid call; the job stays resumable
    with track_costs(db, config, job.id, llm=llm):
        return _write_script(db, job, llm, config)


def _model_checks(
    llm: BaseLLMProvider, draft: ScriptOutput, claims_by_id: dict[str, Claim], model: str
) -> tuple[list[dict], list[str]]:
    """Ask a separate model whether each statement is fully supported by its claims."""
    checks: list[dict] = []
    problems: list[str] = []
    for label, text, claim_ids in _statements(draft, claims_by_id):
        prompt = (
            "Check whether this script statement is fully and accurately supported "
            "by the verified claims. Flag added specificity, unsupported details, "
            "numbers, units, or species names. Use consistent only if every factual "
            "part is supported. If no verified claims are provided, the statement "
            "must be nonfactual framing; otherwise mark it unsupported.\n"
            f"Statement: {text}\n"
            f"Verified claims:\n{_claim_text_for_ids(claim_ids, claims_by_id)}"
        )
        check = llm.generate_structured(prompt, ScriptClaimCheck, model)
        checks.append(
            {
                "statement": text,
                "claim_ids": claim_ids,
                "status": check.status,
                "reason": check.reason,
            }
        )
        if check.status != "consistent":
            problems.append(
                f'The {label} is {check.status} by its claims: "{text}". {check.reason}'
            )
    return checks, problems


def _write_script(db: Session, job: Job, llm: BaseLLMProvider, config: AppConfig) -> bool:
    verified_claims = (
        db.query(Claim).filter(Claim.job_id == job.id, Claim.status == "verified").all()
    )
    if not verified_claims:
        transition_job_state(
            db, job, JobState.DROPPED, reason="Tidak ada klaim verified untuk naskah"
        )
        return False
    if not any(claim.is_hook for claim in verified_claims):
        transition_job_state(db, job, JobState.DROPPED, reason="no_hook_eligible_claim")
        return False

    topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
    topic_text = topic.text if topic else ""
    style_guide = load_style_guide()
    claims_by_id = {claim.id: claim for claim in verified_claims}
    model_heavy = config.settings["models"]["heavy"]
    model_light = config.settings["models"]["light"]
    max_rewrites = int(config.settings.get("script", {}).get("max_rewrites", 2))
    words_per_second = config.settings["speech"]["words_per_second"]

    transition_job_state(db, job, JobState.SCRIPTING, reason="Mulai penulisan naskah")
    feedback: list[str] = []
    last_problem = "unknown"
    attempts_log: list[dict] = []
    for attempt in range(max_rewrites + 1):
        if attempt:
            check_budget(db, config, job.id)  # a rewrite is a new paid stage
        prompt = build_script_prompt(config, topic_text, verified_claims, style_guide, feedback)
        try:
            draft = llm.generate_structured(prompt, ScriptOutput, model_heavy)
        except LLMProviderError as exc:
            transition_job_state(
                db, job, JobState.FAILED, reason=f"Script generation failed: {exc}"
            )
            return False

        transition_job_state(
            db, job, JobState.SCRIPT_CHECK, reason=f"Pemeriksaan naskah #{attempt + 1}"
        )
        problems = script_problems(draft, claims_by_id, config)
        script_checks: list[dict] = []
        if not problems:  # the model check costs money, so only run it on a clean draft
            try:
                script_checks, problems = _model_checks(llm, draft, claims_by_id, model_light)
            except LLMProviderError as exc:
                transition_job_state(db, job, JobState.FAILED, reason=f"Script check failed: {exc}")
                return False

        attempts_log.append(
            {
                "attempt": attempt + 1,
                "draft": draft.model_dump(),
                "problems": list(problems),
                "model_checks": script_checks,
                "outcome": "passed" if not problems else "rejected",
            }
        )
        _save_attempts(job.id, attempts_log)

        if not problems:
            words = sum(count_words(text) for text in _spoken_parts(draft))
            episode = next_episode_number(db, draft.series)
            db.add(
                Script(
                    id=f"scr_{uuid.uuid4().hex[:8]}",
                    job_id=job.id,
                    version=attempt + 1,
                    hook=draft.hook,
                    segments=[s.model_dump() for s in draft.segments],
                    ending=draft.ending,
                    structure=draft.story_structure,
                    check_results={
                        "hook_claim_ids": draft.hook_claim_ids,
                        "ending_kind": draft.ending_kind,
                        "ending_claim_ids": draft.ending_claim_ids,
                        "title_working": draft.title_working,
                        "title_claim_ids": list(claims_by_id),
                        "script_claim_checks": script_checks,
                        "word_count": words,
                        # from the word count, not the model's own guess
                        "estimated_duration_s": round(words / words_per_second),
                        "series": draft.series,
                        "episode_number": episode,
                        "attempts": attempt + 1,
                    },
                    status="passed",
                )
            )
            job.series = draft.series
            job.episode_number = episode
            db.commit()
            transition_job_state(
                db, job, JobState.AWAITING_VOICE, reason="Naskah selesai dan siap direkam"
            )
            return True

        last_problem = problems[0]
        feedback = problems
        logger.info("Script draft %d for %s rejected: %s", attempt + 1, job.id, last_problem)
        if attempt < max_rewrites:
            transition_job_state(
                db,
                job,
                JobState.SCRIPTING,
                reason=f"Tulis ulang naskah ({attempt + 1}/{max_rewrites})",
            )

    transition_job_state(
        db, job, JobState.DROPPED, reason=f"script_check_failed: {last_problem[:200]}"
    )
    return False
