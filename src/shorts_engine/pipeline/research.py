import logging
import uuid
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


def _url_key(url: str) -> str:
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


def select_sources(
    results: list[dict[str, Any]],
    whitelist: dict[str, list[str]],
    max_sources: int,
    per_domain: int,
    max_chars: int,
) -> list[dict[str, Any]]:
    """Keep the best tier A/B pages: unique URLs, a cap per domain, and bounded text.

    Tier is decided from the URL here, never taken from the search provider. Tier C, unlisted
    and near-empty pages are dropped, so they cost nothing downstream.
    """
    candidates = []
    seen_urls: set[str] = set()
    for position, result in enumerate(results):
        url = result.get("url", "")
        host = urlparse(url).hostname
        content = (result.get("content") or "").strip()
        if not host or len(content) < MIN_CONTENT_CHARS:
            continue
        tier = classify_domain(host, whitelist)
        key = _url_key(url)
        if tier not in TIER_RANK or key in seen_urls:
            continue
        seen_urls.add(key)
        candidates.append((TIER_RANK[tier], position, tier, host, result, content))

    chosen: list[dict[str, Any]] = []
    per_domain_count: dict[str, int] = {}
    for _rank, _position, tier, host, result, content in sorted(candidates, key=lambda c: c[:2]):
        independence = source_independence_domain(host, whitelist)
        if per_domain_count.get(independence, 0) >= per_domain:
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
        if len(chosen) >= max_sources:
            break
    return chosen


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
    check_search_quota(db, config, needed=max_queries * _credits_per_search(config))

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

        sources = select_sources(
            results,
            config.source_whitelist,
            int(research.get("max_sources", 6)),
            int(research.get("max_sources_per_domain", 2)),
            int(research.get("max_chars_per_source", 12000)),
        )
        domains = {
            source_independence_domain(source["domain"], config.source_whitelist)
            for source in sources
        }
        minimum = int(research.get("min_distinct_domains", 2))
        if len(domains) < minimum:
            # Fewer than two independent domains can never satisfy the hook rule, so stop here
            # instead of paying Sonnet to read the pages.
            transition_job_state(
                db, job, JobState.DROPPED, reason=f"insufficient_sources:{len(domains)}_domains"
            )
            return False

        job_dir = get_data_dir() / "jobs" / job.id / "research"
        job_dir.mkdir(parents=True, exist_ok=True)
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


def _credits_per_search(config: AppConfig) -> int:
    return 2 if config.settings.get("search", {}).get("depth") == "advanced" else 1
