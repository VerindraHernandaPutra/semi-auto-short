import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from shorts_engine.domain.schemas import ClaimSupportCheck, CorroborationResult
from shorts_engine.pipeline.claim_validation import (
    build_corroboration_prompt,
    build_support_prompt,
    numeric_facts_are_supported,
    quote_similarity,
)
from shorts_engine.pipeline.research import describe_rejections, triage_sources, url_key
from shorts_engine.providers.llm.base import BaseLLMProvider
from shorts_engine.providers.search.base import BaseSearchProvider
from shorts_engine.providers.search.tiers import source_independence_domain

logger = logging.getLogger(__name__)

QUERY_MAX_CHARS = 250

# Outcomes of one attempt to corroborate a claim with one source.
NOT_STATED = "not_stated"  # the model says the source does not state the claim
EMPTY_QUOTE = "empty_quote"  # it said found but gave no quote
QUOTE_NOT_IN_SOURCE = "quote_not_in_source"  # the quote is not in that source's text
NUMBER_MISMATCH = "number_mismatch"  # a number or unit of the claim is not in the quote
CONFIRMED = "confirmed"


@dataclass(frozen=True)
class CorroborationSource:
    """A tier A/B source that might independently state a claim."""

    source_id: str
    domain: str  # independence domain: two sources of the same organisation count once
    text: str


@dataclass(frozen=True)
class Evidence:
    source_id: str
    domain: str
    quote: str
    quote_score: float


@dataclass(frozen=True)
class Attempt:
    """What happened when one source was asked about one claim, kept for the audit trail."""

    source_id: str
    domain: str
    outcome: str  # one of the constants above, or "support_partial" / "support_no"
    reason: str = ""
    quote: str = ""
    quote_score: float | None = None


@dataclass
class Corroboration:
    evidence: list[Evidence] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)

    def as_log(self) -> list[dict[str, Any]]:
        return [asdict(attempt) for attempt in self.attempts]


@dataclass
class Page:
    """A page found by a claim-targeted search: its text plus what is needed to store it."""

    source: CorroborationSource
    url: str
    title: str
    tier: str
    host: str


def _record(
    result: "Corroboration",
    candidate: CorroborationSource,
    outcome: str,
    reason: str = "",
    quote: str = "",
    score: float | None = None,
) -> None:
    result.attempts.append(
        Attempt(candidate.source_id, candidate.domain, outcome, reason, quote, score)
    )


def find_corroboration(
    claim_text: str,
    covered_domains: set[str],
    candidates: list[CorroborationSource],
    llm: BaseLLMProvider,
    model: str,
    threshold: float,
    wanted: int = 1,
) -> Corroboration:
    """Look for independent sources that state the claim, applying the same rules as any claim.

    The model only proposes a quote. A source counts only if the quote is found in that source's
    text, its numbers and units appear in the quote, and a separate check says the quote supports
    the whole claim. Each independence domain is tried once and the search stops as soon as
    ``wanted`` extra domains are confirmed, which keeps the cost bounded. Every attempt is
    recorded with its outcome, so a failure can be explained afterwards.
    """
    covered = set(covered_domains)
    result = Corroboration()
    for candidate in candidates:
        if len(result.evidence) >= wanted:
            break
        if candidate.domain in covered:
            continue
        covered.add(candidate.domain)  # one attempt per domain, successful or not

        answer = llm.generate_structured(
            build_corroboration_prompt(claim_text, candidate.source_id, candidate.text),
            CorroborationResult,
            model,
        )
        quote = answer.supporting_quote.strip()
        if not answer.found:
            _record(result, candidate, NOT_STATED, answer.reason)
            continue
        if not quote:
            _record(result, candidate, EMPTY_QUOTE, answer.reason)
            continue

        score = quote_similarity(candidate.text, quote)
        if score < threshold:
            _record(result, candidate, QUOTE_NOT_IN_SOURCE, answer.reason, quote, score)
            continue
        if not numeric_facts_are_supported(claim_text, quote):
            _record(result, candidate, NUMBER_MISMATCH, answer.reason, quote, score)
            continue

        check = llm.generate_structured(
            build_support_prompt(claim_text, quote), ClaimSupportCheck, model
        )
        if check.status != "supports":
            _record(result, candidate, f"support_{check.status}", check.reason, quote, score)
            continue
        _record(result, candidate, CONFIRMED, check.reason, quote, score)
        result.evidence.append(Evidence(candidate.source_id, candidate.domain, quote, score))
    return result


def find_corroboration_pages(
    search: BaseSearchProvider,
    claim_text: str,
    whitelist: dict[str, list[str]],
    known_urls: set[str],
    max_sources: int,
    per_domain: int,
    max_chars: int,
) -> tuple[list[Page], dict[str, Any]]:
    """Search for pages that might state this one claim, and keep the usable tier A/B ones.

    This is the claim-targeted search: the first research round is about the topic as a whole,
    so its pages rarely share a specific fact. URLs come only from the search provider. Pages
    already used as sources are skipped. Failures raise SearchProviderError.
    """
    query = " ".join(claim_text.split())[:QUERY_MAX_CHARS]
    results = search.search_and_extract([query])
    chosen, rejected = triage_sources(results, whitelist, max_sources, per_domain, max_chars)
    pages = []
    for item in chosen:
        if url_key(item["url"]) in known_urls:
            continue
        domain = source_independence_domain(item["domain"], whitelist)
        source = CorroborationSource(f"src_{uuid.uuid4().hex[:8]}", domain, item["content"])
        pages.append(Page(source, item["url"], item["title"], item["tier"], item["domain"]))
    info = {
        "query": query,
        "results": len(results),
        "usable_domains": sorted({page.source.domain for page in pages}),
        "rejected": describe_rejections(rejected) or "none",
    }
    return pages, info
