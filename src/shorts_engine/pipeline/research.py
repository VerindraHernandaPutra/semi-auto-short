import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Job, Source, Topic
from shorts_engine.domain.schemas import SearchQueries
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.providers.factory import get_llm_provider, get_search_provider
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError
from shorts_engine.providers.search.base import BaseSearchProvider, SearchProviderError
from shorts_engine.providers.search.tiers import classify_domain, source_independence_domain
from shorts_engine.services.costs import check_budget, check_search_quota, track_costs

logger = logging.getLogger(__name__)

LIGHT_EFFORT = "low"
MIN_CONTENT_CHARS = 500  # shorter pages are navigation stubs, not usable evidence
TIER_RANK = {"tier_a": 0, "tier_b": 1}


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+")
MIN_PROSE_WORDS = 8


def clean_page_text(text: str, min_words: int = MIN_PROSE_WORDS) -> str:
    """Keep the prose of a web page and drop menus, logos and link lists.

    Search providers return pages with their navigation. NASA pages, for example, spend the first
    ~9,000 characters on menus, so a fixed cut-off could remove the article itself and the model
    would be paid to read the menu. Lines of fewer than ``min_words`` words are treated as
    navigation. Stored text is this cleaned text, so quotes are verified against what was read.
    """
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _BARE_URL.sub(" ", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if len(line.split()) >= min_words)


def url_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/')}"


def clean_queries(queries: list[str], max_queries: int) -> list[str]:
    """Strip, drop empties and case-insensitive duplicates, and cap the count (SPEC 7.2)."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for query in queries:
        text = " ".join(query.split())
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            cleaned.append(text)
    return cleaned[:max_queries]


def triage_sources(
    results: list[dict[str, Any]],
    whitelist: dict[str, list[str]],
    max_sources: int,
    per_domain: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Split search results into the pages to keep and the pages rejected, with the reason.

    Kept pages are the best tier A/B ones: unique URLs, a cap per domain, bounded text. Tier is
    decided from the URL here, never taken from the search provider. Every rejected page gets a
    reason (no_host, unlisted, tier_c, too_short, duplicate_url, domain_cap, over_limit) so that
    an empty result can be explained instead of guessed at.
    """
    rejected: list[dict[str, str]] = []

    def reject(url: str, host: str | None, reason: str) -> None:
        domain = (host or "").lower().removeprefix("www.")
        rejected.append({"domain": domain, "url": url, "reason": reason})

    candidates = []
    seen_urls: set[str] = set()
    for position, result in enumerate(results):
        url = result.get("url", "")
        host = urlparse(url).hostname
        content = clean_page_text(result.get("content") or "")
        if not host:
            reject(url, host, "no_host")
            continue
        tier = classify_domain(host, whitelist)
        if tier not in TIER_RANK:
            reject(url, host, tier)  # "tier_c" or "unlisted"
            continue
        if len(content) < MIN_CONTENT_CHARS:
            reject(url, host, "too_short")
            continue
        key = url_key(url)
        if key in seen_urls:
            reject(url, host, "duplicate_url")
            continue
        seen_urls.add(key)
        candidates.append((TIER_RANK[tier], position, tier, host, result, content))

    chosen: list[dict[str, Any]] = []
    per_domain_count: dict[str, int] = {}
    for _rank, _position, tier, host, result, content in sorted(candidates, key=lambda c: c[:2]):
        independence = source_independence_domain(host, whitelist)
        if per_domain_count.get(independence, 0) >= per_domain:
            reject(result["url"], host, "domain_cap")
            continue
        if len(chosen) >= max_sources:
            reject(result["url"], host, "over_limit")
            continue
        per_domain_count[independence] = per_domain_count.get(independence, 0) + 1
        chosen.append(
            {
                "url": result["url"],
                "domain": host.lower().removeprefix("www."),
                "title": result.get("title", ""),
                "tier": tier,
                "content": content[:max_chars],
            }
        )
    return chosen, rejected


def select_sources(
    results: list[dict[str, Any]],
    whitelist: dict[str, list[str]],
    max_sources: int,
    per_domain: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    """The pages that triage_sources keeps."""
    return triage_sources(results, whitelist, max_sources, per_domain, max_chars)[0]


def describe_rejections(rejected: list[dict[str, str]]) -> str:
    """Short count per reason for a job event, e.g. "5 unlisted, 1 tier_c"."""
    counts: dict[str, int] = {}
    for item in rejected:
        counts[item["reason"]] = counts.get(item["reason"], 0) + 1
    return ", ".join(f"{count} {reason}" for reason, count in sorted(counts.items()))


def write_research_summary(
    job_dir: Path,
    queries: list[str],
    total_results: int,
    chosen: list[dict[str, Any]],
    rejected: list[dict[str, str]],
) -> None:
    """Keep what the search returned and why pages were rejected, even for a dropped topic."""
    job_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "queries": queries,
        "results_total": total_results,
        "kept": [{"domain": s["domain"], "tier": s["tier"], "url": s["url"]} for s in chosen],
        "rejected": rejected,
    }
    (job_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def build_query_prompt(topic_text: str, max_queries: int) -> str:
    return f"""You plan web research for a fact-checked science video.
The topic below is a hypothesis to test, not an established fact.

Topic: {topic_text}

Write 3 to {max_queries} English web search queries. Together they should find primary scientific
sources (universities, research institutions, government science agencies, reputable science
publishers). Each query targets a different question a careful fact-checker would ask: the
mechanism, measurements, the species or object involved, and common misconceptions.
Do not include site names or URLs."""


def run_research(
    db: Session,
    job: Job,
    llm: BaseLLMProvider | None = None,
    search: BaseSearchProvider | None = None,
    config: AppConfig | None = None,
) -> bool:
    config = config or get_config()
    llm = llm or get_llm_provider()
    search = search or get_search_provider()
    research = config.settings.get("research", {})
    max_queries = int(research.get("max_queries", 6))

    topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
    if not topic:
        transition_job_state(db, job, JobState.FAILED, reason="Topik tidak ditemukan")
        return False

    # Both checks run before any state change or paid call, so a refused job stays resumable.
    check_budget(db, config, job.id)
    check_search_quota(db, config, needed=max_queries * credits_per_search(config))

    with track_costs(db, config, job.id, llm=llm, search=search):
        transition_job_state(db, job, JobState.RESEARCHING, reason="Mulai pencarian sumber")
        model_name = config.settings["models"]["light"]
        try:
            generated = llm.generate_structured(
                build_query_prompt(topic.text, max_queries),
                SearchQueries,
                model_name,
                effort=LIGHT_EFFORT,
            )
        except LLMProviderError as exc:
            transition_job_state(db, job, JobState.FAILED, reason=f"Gagal membuat query: {exc}")
            return False
        queries = clean_queries(generated.queries, max_queries)
        if not queries:
            transition_job_state(db, job, JobState.FAILED, reason="Tidak ada query yang valid")
            return False

        try:
            results = search.search_and_extract(queries)
        except SearchProviderError as exc:
            transition_job_state(db, job, JobState.FAILED, reason=f"Pencarian gagal: {exc}")
            return False

        sources, rejected = triage_sources(
            results,
            config.source_whitelist,
            int(research.get("max_sources", 6)),
            int(research.get("max_sources_per_domain", 2)),
            int(research.get("max_chars_per_source", 12000)),
        )
        job_dir = get_data_dir() / "jobs" / job.id / "research"
        write_research_summary(job_dir, queries, len(results), sources, rejected)
        domains = {
            source_independence_domain(source["domain"], config.source_whitelist)
            for source in sources
        }
        minimum = int(research.get("min_distinct_domains", 2))
        if len(domains) < minimum:
            # Fewer than two independent domains can never satisfy the hook rule, so stop here
            # instead of paying Sonnet to read the pages.
            detail = describe_rejections(rejected) or "no results"
            transition_job_state(
                db,
                job,
                JobState.DROPPED,
                reason=(
                    f"insufficient_sources:{len(domains)}_domains "
                    f"({len(results)} results; rejected: {detail})"
                ),
            )
            return False

        for source in sources:
            source_id = f"src_{uuid.uuid4().hex[:8]}"
            file_path = job_dir / f"{source_id}.txt"
            file_path.write_text(source["content"], encoding="utf-8")
            db.add(
                Source(
                    id=source_id,
                    job_id=job.id,
                    url=source["url"],
                    domain=source["domain"],
                    title=source["title"],
                    tier=source["tier"],
                    extracted_text_path=str(file_path),
                )
            )
        db.commit()
        transition_job_state(db, job, JobState.VERIFYING, reason="Riset selesai, lanjut verifikasi")
        return True


def credits_per_search(config: AppConfig) -> int:
    return 2 if config.settings.get("search", {}).get("depth") == "advanced" else 1
