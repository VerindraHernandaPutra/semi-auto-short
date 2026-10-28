import logging
from dataclasses import dataclass

from shorts_engine.domain.schemas import ClaimSupportCheck, CorroborationResult
from shorts_engine.pipeline.claim_validation import (
    build_corroboration_prompt,
    build_support_prompt,
    numeric_facts_are_supported,
    quote_similarity,
)
from shorts_engine.providers.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


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


def find_corroboration(
    claim_text: str,
    covered_domains: set[str],
    candidates: list[CorroborationSource],
    llm: BaseLLMProvider,
    model: str,
    threshold: float,
    wanted: int = 1,
) -> list[Evidence]:
    """Look for independent sources that state the claim, applying the same rules as any claim.

    The model only proposes a quote. A source counts only if the quote is found in that source's
    text, its numbers and units appear in the quote, and a separate check says the quote supports
    the whole claim. Each independence domain is tried once and the search stops as soon as
    ``wanted`` extra domains are confirmed, which keeps the cost bounded.
    """
    covered = set(covered_domains)
    evidence: list[Evidence] = []
    for candidate in candidates:
        if len(evidence) >= wanted:
            break
        if candidate.domain in covered:
            continue
        covered.add(candidate.domain)  # one attempt per domain, successful or not

        result = llm.generate_structured(
            build_corroboration_prompt(claim_text, candidate.source_id, candidate.text),
            CorroborationResult,
            model,
        )
        quote = result.supporting_quote.strip()
        if not result.found or not quote:
            continue

        score = quote_similarity(candidate.text, quote)
        if score < threshold:
            logger.info("Corroboration quote not found in %s", candidate.source_id)
            continue
        if not numeric_facts_are_supported(claim_text, quote):
            logger.info("Corroboration quote lacks the claim's numbers in %s", candidate.source_id)
            continue

        check = llm.generate_structured(
            build_support_prompt(claim_text, quote), ClaimSupportCheck, model
        )
        if check.status != "supports":
            continue
        evidence.append(Evidence(candidate.source_id, candidate.domain, quote, score))
    return evidence
