import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config, load_style_guide
from shorts_engine.db.models import Topic
from shorts_engine.domain.schemas import TopicCandidate, TopicList, TopicPolicyCheck
from shorts_engine.providers.factory import get_footage_provider, get_llm_provider
from shorts_engine.providers.footage.base import BaseFootageProvider
from shorts_engine.providers.llm.base import BaseLLMProvider
from shorts_engine.services.costs import BudgetExceeded, check_budget, make_usage_listener

logger = logging.getLogger(__name__)

LIGHT_EFFORT = "low"
KNOWN_TEXT_LIMIT = 60  # how many existing topics are shown to the model to avoid repeats
MIN_CLIP_HEIGHT = 720
STOPWORDS = frozenset(
    "a an and are as at be by can do does did for from how in into is it its of on or "
    "that the their this to was what when where which why with without you your".split()
)

STATUS_QUEUED = "queued"
STATUS_REJECTED = "rejected"


class TopicGenerationError(RuntimeError):
    """The topic queue could not be refilled because a required service failed."""


@dataclass
class RefillReport:
    queued: list[Topic] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    batches: int = 0


# --- duplicate detection -------------------------------------------------------------------


def _stem(word: str) -> str:
    """Cheap suffix stripping so 'landing', 'lands' and 'land' compare equal."""
    if word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("ses") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("es") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us")) and len(word) > 3:
        word = word[:-1]
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def content_tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(_stem(word) for word in words if word not in STOPWORDS)


def find_duplicate(text: str, known_texts: list[str], threshold: float) -> str | None:
    """Return the known topic that has the same substance as ``text``, if any.

    Uses word overlap on stemmed content words, so a reworded title does not count as new.
    Embedding-based matching is a later improvement (SPEC 7.1).
    """
    tokens = content_tokens(text)
    if not tokens:
        return None
    for known in known_texts:
        known_tokens = content_tokens(known)
        if not known_tokens:
            continue
        overlap = len(tokens & known_tokens) / len(tokens | known_tokens)
        if overlap >= threshold:
            return known
    return None


def find_overused(text: str, overused: list[str], containment: float = 0.75) -> str | None:
    """Return the overused topic that ``text`` restates, if any.

    A candidate restates an overused topic when most of that topic's content words appear in it.
    """
    tokens = content_tokens(text)
    for phrase in overused:
        phrase_tokens = content_tokens(phrase)
        if phrase_tokens and len(tokens & phrase_tokens) / len(phrase_tokens) >= containment:
            return phrase
    return None


def is_generic_subject(subject: str, generic_subjects: list[str]) -> bool:
    """True when the subject is empty or made only of broad words such as 'cat' or 'ocean'."""
    tokens = content_tokens(subject)
    generic = frozenset(token for word in generic_subjects for token in content_tokens(word))
    return not tokens or tokens <= generic


# --- prompts -------------------------------------------------------------------------------


def build_generation_prompt(
    config: AppConfig,
    style_guide: str,
    count: int,
    known_texts: list[str],
    category_counts: Counter,
    series_counts: Counter,
) -> str:
    policy = config.topic_policy
    overused = "\n".join(f"- {item}" for item in policy.get("overused_topics", [])) or "- (none)"
    generic = ", ".join(policy.get("generic_subjects", [])) or "(none)"
    series_lines = "\n".join(
        f'- {item["name"]}: structure {item["structure"]}; hook pattern "{item["hook_pattern"]}"'
        for item in policy["series"]
    )
    known = "\n".join(f"- {text}" for text in known_texts[:KNOWN_TEXT_LIMIT]) or "- (none yet)"
    language = config.settings["content_language"]
    return f"""You propose topics for short educational science videos (35-55 seconds each).
Write every field in the language with code "{language}", except footage_queries, which are
always English because they are used to search stock video sites.

Channel style guide:
---
{style_guide}
---

Allowed categories: {", ".join(policy["active_categories"])}
Series (every topic belongs to exactly one):
{series_lines}
Forbidden subject areas (never propose these): {", ".join(policy["forbidden_topics"])}

Overused topics that countless channels already cover. Never propose these or close variants:
{overused}

Too-broad subjects. A topic whose subject is only one of these words is rejected: {generic}

Existing topics. Do not repeat or reword any of them:
{known}

Queue balance so far. Favour categories and series that are underrepresented:
categories: {dict(category_counts) or "none"}; series: {dict(series_counts) or "none"}

Propose exactly {count} new topics. Rules:
- Be specific. Name a particular subject (one planet, moon, star, object or phenomenon) and one
  narrow angle on it (a single mechanism, measurement or surprising detail). Prefer subjects that
  popular science videos rarely cover, as long as they can still be filmed. Every topic needs a
  different subject.
- Each topic must be answerable within one short video, not a broad subject.
- Pick subjects that commonly exist as stock video (planets, moons, the Sun, stars, nebulae,
  galaxies, night skies, telescopes). Avoid topics that need diagrams, charts or abstract
  processes that cannot be shown.
- Do not state scientific facts as certain; you are proposing ideas that will be fact-checked later.
- footage_queries: 2 to 3 short queries that name the specific subject itself, so we can test
  whether stock video of that exact subject exists.
- Scores are integers from 1 to 10. Be critical: specificity and novelty of 8 or more are rare."""


def build_policy_prompt(candidates: list[TopicCandidate], forbidden: list[str]) -> str:
    listing = "\n".join(f"[{index}] {item.text}" for index, item in enumerate(candidates))
    return f"""Judge each candidate topic for a family-friendly science channel.
List the forbidden categories a topic falls under, or an empty list if it is acceptable.
Forbidden categories: {", ".join(forbidden)}
Return one verdict for every candidate index.

Candidates:
{listing}"""


# --- filters -------------------------------------------------------------------------------


def policy_rejections(
    llm: BaseLLMProvider, config: AppConfig, candidates: list[TopicCandidate], model: str
) -> dict[int, str]:
    """Map candidate index to a rejection reason. A missing verdict counts as a rejection."""
    forbidden = list(config.topic_policy["forbidden_topics"])
    check = llm.generate_structured(
        build_policy_prompt(candidates, forbidden), TopicPolicyCheck, model, effort=LIGHT_EFFORT
    )
    verdicts = {verdict.index: verdict for verdict in check.verdicts}
    rejected: dict[int, str] = {}
    for index in range(len(candidates)):
        verdict = verdicts.get(index)
        if verdict is None:
            rejected[index] = "policy_unchecked"
        elif verdict.violated_categories:
            rejected[index] = "policy:" + ",".join(verdict.violated_categories)
    return rejected


def check_footage(
    candidate: TopicCandidate, footage: BaseFootageProvider, queue_settings: dict[str, Any]
) -> dict[str, Any]:
    """Count distinct usable stock clips for the candidate's footage queries."""
    min_seconds = float(queue_settings.get("min_clip_seconds", 5))
    usable: dict[str, str] = {}
    for query in candidate.footage_queries[:3]:
        for clip in footage.search_videos(query, per_page=20):
            if clip.duration_s >= min_seconds and clip.height >= MIN_CLIP_HEIGHT:
                usable.setdefault(clip.asset_id, clip.provider)
    return {
        "queries": candidate.footage_queries[:3],
        "usable_clips": len(usable),
        "sample_asset_ids": list(usable)[:10],
        "provider": next(iter(usable.values()), None),
    }


def score_topic(
    candidate: TopicCandidate, category_counts: Counter, series_counts: Counter
) -> tuple[float, str]:
    """Weighted blend of the four ratings, plus a bonus for underused categories and series."""
    base = (
        0.3 * candidate.visual_potential_score
        + 0.3 * candidate.hook_surprise_score
        + 0.2 * candidate.specificity_score
        + 0.2 * candidate.novelty_score
    )
    category_bonus = category_counts[candidate.category] <= min(category_counts.values(), default=0)
    series_bonus = series_counts[candidate.series] <= min(series_counts.values(), default=0)
    diversity = 0.5 * (int(category_bonus) + int(series_bonus))
    reason = (
        f"visual {candidate.visual_potential_score}, hook surprise {candidate.hook_surprise_score}, "
        f"specificity {candidate.specificity_score}, novelty {candidate.novelty_score}; "
        f"diversity bonus {diversity}; subject: {candidate.subject}"
    )
    return round(base + diversity, 2), reason


# --- queue refill --------------------------------------------------------------------------


def _new_topic(candidate: TopicCandidate, status: str, reason: str | None, **extra: Any) -> Topic:
    return Topic(
        id=f"top_{uuid.uuid4().hex[:8]}",
        text=candidate.text,
        category=candidate.category,
        series=candidate.series,
        status=status,
        reason=reason,
        **extra,
    )


def _process_batch(
    db: Session,
    config: AppConfig,
    llm: BaseLLMProvider,
    footage: BaseFootageProvider,
    batch_size: int,
    report: RefillReport,
) -> list[Topic]:
    queue_settings = config.settings.get("topic_queue", {})
    model = config.settings["models"]["light"]
    threshold = float(queue_settings.get("duplicate_similarity", 0.5))
    existing = db.query(Topic).all()
    known_texts = [topic.text for topic in existing]
    queued_now = [topic for topic in existing if topic.status == STATUS_QUEUED]
    category_counts = Counter(topic.category for topic in queued_now)
    series_counts = Counter(topic.series for topic in queued_now)
    for name in config.topic_policy["active_categories"]:
        category_counts.setdefault(name, 0)
    for item in config.topic_policy["series"]:
        series_counts.setdefault(item["name"], 0)

    min_specificity = int(queue_settings.get("min_specificity", 7))
    min_novelty = int(queue_settings.get("min_novelty", 6))
    overused = list(config.topic_policy.get("overused_topics", []))
    generic = list(config.topic_policy.get("generic_subjects", []))

    prompt = build_generation_prompt(
        config,
        load_style_guide(),
        batch_size,
        list(reversed(known_texts)),
        category_counts,
        series_counts,
    )
    candidates = llm.generate_structured(prompt, TopicList, model, effort=LIGHT_EFFORT).topics

    rejected: dict[int, str] = {}
    series_names = {item["name"] for item in config.topic_policy["series"]}
    seen = list(known_texts)
    seen_subjects: set[frozenset[str]] = set()
    for index, candidate in enumerate(candidates):
        subject_key = content_tokens(candidate.subject)
        if candidate.category not in config.topic_policy["active_categories"]:
            rejected[index] = "unknown_category"
        elif candidate.series not in series_names:
            rejected[index] = "unknown_series"
        elif is_generic_subject(candidate.subject, generic):
            rejected[index] = f"generic_subject:{candidate.subject[:40]}"
        elif (restated := find_overused(candidate.text, overused)) is not None:
            rejected[index] = f"overused:{restated}"
        elif subject_key in seen_subjects:
            rejected[index] = f"duplicate_subject:{candidate.subject[:40]}"
        elif candidate.specificity_score < min_specificity:
            rejected[index] = f"low_specificity:{candidate.specificity_score}"
        elif candidate.novelty_score < min_novelty:
            rejected[index] = f"low_novelty:{candidate.novelty_score}"
        elif (match := find_duplicate(candidate.text, seen, threshold)) is not None:
            rejected[index] = f"duplicate_of:{match[:60]}"
        else:
            seen.append(candidate.text)
            seen_subjects.add(subject_key)

    survivors = [i for i in range(len(candidates)) if i not in rejected]
    if survivors:
        policy = policy_rejections(llm, config, [candidates[i] for i in survivors], model)
        for position, reason in policy.items():
            rejected[survivors[position]] = reason

    accepted: list[Topic] = []
    min_clips = int(queue_settings.get("min_footage_candidates", 3))
    for index, candidate in enumerate(candidates):
        if index in rejected:
            db.add(_new_topic(candidate, STATUS_REJECTED, rejected[index]))
            report.rejected.append((candidate.text, rejected[index]))
            continue
        check = check_footage(candidate, footage, queue_settings)
        if check["usable_clips"] < min_clips:
            reason = f"insufficient_footage:{check['usable_clips']}"
            db.add(_new_topic(candidate, STATUS_REJECTED, reason, footage_check=check))
            report.rejected.append((candidate.text, reason))
            continue
        score, score_reason = score_topic(candidate, category_counts, series_counts)
        topic = _new_topic(
            candidate,
            STATUS_QUEUED,
            None,
            score=score,
            score_reason=f"{score_reason}. Hook idea: {candidate.hook_idea}",
            footage_check=check,
        )
        db.add(topic)
        accepted.append(topic)
        category_counts[candidate.category] += 1
        series_counts[candidate.series] += 1
    db.commit()
    return accepted


def refill_topic_queue(
    db: Session,
    target: int | None = None,
    llm: BaseLLMProvider | None = None,
    footage: BaseFootageProvider | None = None,
    config: AppConfig | None = None,
) -> RefillReport:
    """Generate, filter, score and queue topics until the queue holds ``target`` topics."""
    config = config or get_config()
    llm = llm or get_llm_provider()
    footage = footage or get_footage_provider()
    queue_settings = config.settings.get("topic_queue", {})
    target = target if target is not None else int(queue_settings.get("min_queue_size", 10))
    batch_size = int(queue_settings.get("generation_batch_size", 12))
    max_batches = int(queue_settings.get("max_batches", 3))

    report = RefillReport()
    previous_listener = llm.usage_listener
    llm.usage_listener = make_usage_listener(db, config)  # topic costs have no job
    try:
        while report.batches < max_batches:
            queued = db.query(Topic).filter(Topic.status == STATUS_QUEUED).count()
            needed = target - queued
            if needed <= 0:
                break
            check_budget(db, config)  # raises BudgetExceeded before any paid call
            # Ask for more than needed because some candidates are filtered out.
            asked = min(batch_size, max(needed * 2, 4))
            try:
                report.queued += _process_batch(db, config, llm, footage, asked, report)
            except BudgetExceeded:
                raise
            except Exception as exc:
                db.rollback()
                raise TopicGenerationError(f"topic refill failed: {exc}") from exc
            report.batches += 1
    finally:
        llm.usage_listener = previous_listener
    return report


def generate_topics(db: Session, count: int = 3) -> list[Topic]:
    """Queue up to ``count`` additional topics and return the newly queued ones."""
    queued = db.query(Topic).filter(Topic.status == STATUS_QUEUED).count()
    return refill_topic_queue(db, target=queued + count).queued
