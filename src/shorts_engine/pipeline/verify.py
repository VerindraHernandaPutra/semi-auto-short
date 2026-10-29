import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Claim, Job, Source, Topic
from shorts_engine.domain.schemas import (
    ClaimCandidate,
    ClaimSupportCheck,
    ExtractedClaims,
)
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.claim_validation import (
    build_extraction_prompt,
    build_support_prompt,
    claim_key,
    claim_overlap,
    numeric_facts_are_supported,
    quote_similarity,
)
from shorts_engine.pipeline.corroboration import (
    CorroborationSource,
    Evidence,
    Page,
    find_corroboration,
    find_corroboration_pages,
)
from shorts_engine.pipeline.research import credits_per_search, url_key
from shorts_engine.providers.factory import (
    get_llm_provider,
    get_search_provider,
    is_mock_provider,
)
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError
from shorts_engine.providers.search.base import BaseSearchProvider, SearchProviderError
from shorts_engine.providers.search.tiers import (
    classify_domain,
    source_independence_domain,
)
from shorts_engine.services.costs import check_budget, check_search_quota, track_costs


@dataclass
class CandidateAssessment:
    candidate: ClaimCandidate
    source: Source | None
    quote_score: float
    support_check: str
    reject_reason: str | None
    verified: bool = False
    hook_ok: bool = False  # confirmed by at least hook_min_domains independent domains
    evidence: list[Evidence] = field(default_factory=list)  # extra sources found for the hook


def _source_domain(source: Source) -> str | None:
    hostname = urlparse(source.url).hostname
    if not hostname:
        return None
    return hostname.lower().removeprefix("www.").rstrip(".")


def run_verify(
    db: Session,
    job: Job,
    llm: BaseLLMProvider | None = None,
    search: BaseSearchProvider | None = None,
    config: AppConfig | None = None,
) -> bool:
    config = config or get_config()
    llm = llm or get_llm_provider()
    search = search or get_search_provider()

    if is_mock_provider(llm) or is_mock_provider(search):
        transition_job_state(
            db,
            job,
            JobState.DROPPED,
            reason="mock_provider_not_allowed_for_fact_verification",
        )
        return False

    # Refused before any paid call, so the job stays resumable.
    check_budget(db, config, job.id)
    verification = config.settings.get("verification", {})
    if verification.get("corroboration_search", True):
        searches = int(verification.get("max_corroboration_searches", 4))
        check_search_quota(db, config, needed=searches * credits_per_search(config))
    with track_costs(db, config, job.id, llm=llm, search=search):
        return _verify(db, job, llm, search, config)


def _write_verification_log(job_dir: Path, log: list[dict]) -> None:
    """Keep every hook attempt with its outcome, so a dropped topic can be explained."""
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "verification.json").write_text(
        json.dumps({"hook_candidates": log}, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _verify(
    db: Session,
    job: Job,
    llm: BaseLLMProvider,
    search: BaseSearchProvider,
    config: AppConfig,
) -> bool:
    all_sources = db.query(Source).filter(Source.job_id == job.id).all()
    whitelist = config.source_whitelist
    sources = {}
    source_texts = {}
    for source in all_sources:
        domain = _source_domain(source)
        tier = classify_domain(domain or "", whitelist)
        if tier not in {"tier_a", "tier_b"}:
            continue
        sources[source.id] = source
        if not source.extracted_text_path:
            transition_job_state(
                db,
                job,
                JobState.FAILED,
                reason=f"Extracted source text path is missing for {source.id}",
            )
            return False
        try:
            with open(source.extracted_text_path, encoding="utf-8") as source_file:
                source_texts[source.id] = source_file.read()
        except OSError:
            transition_job_state(
                db,
                job,
                JobState.FAILED,
                reason=f"Cannot read extracted source text for {source.id}",
            )
            return False

    if not sources:
        transition_job_state(db, job, JobState.DROPPED, reason="Tidak ada sumber Tier A/B")
        return False

    model_heavy = config.settings.get("models", {}).get("heavy", "claude-sonnet-5-5")
    model_light = config.settings.get("models", {}).get("light", "claude-haiku-5-5")
    source_text = "\n".join(
        f"\n--- ID: {source_id} ---\n{content}" for source_id, content in source_texts.items()
    )
    topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
    prompt_extract = build_extraction_prompt(
        topic.text if topic else "",
        source_text,
        int(config.settings.get("verification", {}).get("max_claims_extracted", 12)),
    )

    try:
        extracted = llm.generate_structured(prompt_extract, ExtractedClaims, model_heavy)
    except LLMProviderError as exc:
        transition_job_state(db, job, JobState.FAILED, reason=f"Claim extraction failed: {exc}")
        return False

    threshold = float(
        config.settings.get("verification", {}).get("quote_similarity_threshold", 0.92)
    )
    assessments = []
    for candidate in extracted.claims:
        source = sources.get(candidate.source_id)
        assessment = CandidateAssessment(
            candidate=candidate,
            source=source,
            quote_score=0.0,
            support_check="not_run",
            reject_reason=None,
        )
        assessments.append(assessment)

        if source is None:
            assessment.reject_reason = "source_not_in_verified_tier_a_or_b"
            continue

        source_content = source_texts[source.id]
        assessment.quote_score = quote_similarity(source_content, candidate.supporting_quote)
        if assessment.quote_score < threshold:
            assessment.reject_reason = "supporting_quote_not_found"
            continue

        if not numeric_facts_are_supported(candidate.text, candidate.supporting_quote):
            assessment.reject_reason = "claim_number_or_unit_not_in_quote"
            continue

        prompt_check = build_support_prompt(candidate.text, candidate.supporting_quote)
        try:
            check_result = llm.generate_structured(prompt_check, ClaimSupportCheck, model_light)
        except LLMProviderError as exc:
            transition_job_state(
                db, job, JobState.FAILED, reason=f"Claim support check failed: {exc}"
            )
            return False

        assessment.support_check = check_result.status
        if check_result.status == "supports":
            assessment.verified = True
        else:
            assessment.reject_reason = check_result.reason or "claim_not_supported"

    verification = config.settings.get("verification", {})
    hook_min_domains = int(verification.get("hook_min_domains", 2))
    max_hook_candidates = int(verification.get("max_hook_candidates", 4))

    def independence_domain(source: Source) -> str:
        return source_independence_domain(_source_domain(source) or "", whitelist)

    ordered_sources = sorted(
        sources.values(),
        key=lambda source: (
            0 if classify_domain(_source_domain(source) or "", whitelist) == "tier_a" else 1
        ),
    )
    corroboration_pool = [
        CorroborationSource(source.id, independence_domain(source), source_texts[source.id])
        for source in ordered_sources
    ]

    known_urls = {url_key(source.url) for source in all_sources}
    max_searches = int(verification.get("max_corroboration_searches", 4))
    search_enabled = bool(verification.get("corroboration_search", True))
    research_cfg = config.settings.get("research", {})
    searches_done = 0
    extra_pages: dict[str, Page] = {}  # pages found by claim searches, by generated source id
    hook_log: list[dict] = []

    def two_domains_confirmed(group: list[CandidateAssessment]) -> bool:
        """True if the claim stands on enough independent domains, searching for more if needed.

        First the pages already collected are asked; if that is not enough, one search made for
        this exact claim brings in new pages. Both routes apply the same checks.
        """
        nonlocal searches_done
        claim_text = group[0].candidate.text
        domains = {independence_domain(a.source) for a in group if a.source is not None}
        entry: dict = {
            "claim": claim_text,
            "supporting_domains": sorted(domains),
            "attempts": [],
            "search": None,
        }
        hook_log.append(entry)

        if len(domains) < hook_min_domains:
            first = find_corroboration(
                claim_text,
                domains,
                corroboration_pool,
                llm,
                model_light,
                threshold,
                wanted=hook_min_domains - len(domains),
            )
            group[0].evidence = list(first.evidence)
            entry["attempts"] += first.as_log()
            domains |= {e.domain for e in first.evidence}

        if len(domains) < hook_min_domains and search_enabled and searches_done < max_searches:
            check_budget(db, config, job.id)
            searches_done += 1
            pages, info = find_corroboration_pages(
                search,
                claim_text,
                whitelist,
                known_urls,
                int(research_cfg.get("max_sources", 6)),
                int(research_cfg.get("max_sources_per_domain", 2)),
                int(research_cfg.get("max_chars_per_source", 12000)),
            )
            entry["search"] = info
            for page in pages:
                extra_pages[page.source.source_id] = page
                known_urls.add(url_key(page.url))
            second = find_corroboration(
                claim_text,
                domains,
                [page.source for page in pages],
                llm,
                model_light,
                threshold,
                wanted=hook_min_domains - len(domains),
            )
            group[0].evidence += second.evidence
            entry["attempts"] += second.as_log()
            domains |= {e.domain for e in second.evidence}

        entry["confirmed"] = len(domains) >= hook_min_domains
        return entry["confirmed"]

    def group_by_claim(
        candidates: list[CandidateAssessment],
    ) -> dict[str, list[CandidateAssessment]]:
        groups: dict[str, list[CandidateAssessment]] = {}
        for assessment in candidates:
            groups.setdefault(claim_key(assessment.candidate.text), []).append(assessment)
        return groups

    verified_hook_keys: set[str] = set()
    tried = 0
    try:
        # 1. The claim the model flagged as the hook. If it cannot stand on two domains it is
        #    rejected, as before: the model's most striking claim was not well supported.
        flagged = group_by_claim([a for a in assessments if a.candidate.is_hook and a.verified])
        for key, group in flagged.items():
            if tried >= max_hook_candidates:
                break
            tried += 1
            if two_domains_confirmed(group):
                verified_hook_keys.add(key)
                for assessment in group:
                    assessment.hook_ok = True
                break
            for assessment in group:
                assessment.verified = False
                assessment.reject_reason = "hook_requires_two_independent_domains"

        # 2. The model cannot know which claim several sources share. Any other verified claim
        #    may be the hook if it passes the very same two-domain rule; one that fails simply
        #    stays an ordinary single-source claim, which the spec allows.
        if not verified_hook_keys:
            others = group_by_claim([a for a in assessments if a.verified and not a.hook_ok])

            def shared_by(group: list[CandidateAssessment]) -> int:
                """Independent domains, besides the claim's own, whose text seems to state it."""
                own = {independence_domain(a.source) for a in group if a.source is not None}
                return len(
                    {
                        page.domain
                        for page in corroboration_pool
                        if page.domain not in own
                        and claim_overlap(group[0].candidate.text, page.text)
                    }
                )

            ranked = sorted(others.items(), key=lambda item: -shared_by(item[1]))
            for key, group in ranked:
                if tried >= max_hook_candidates:
                    break
                tried += 1
                if two_domains_confirmed(group):
                    verified_hook_keys.add(key)
                    for assessment in group:
                        assessment.hook_ok = True
                    break
    except LLMProviderError as exc:
        _write_verification_log(get_data_dir() / "jobs" / job.id / "research", hook_log)
        transition_job_state(db, job, JobState.FAILED, reason=f"Hook corroboration failed: {exc}")
        return False
    except SearchProviderError as exc:
        _write_verification_log(get_data_dir() / "jobs" / job.id / "research", hook_log)
        transition_job_state(
            db, job, JobState.FAILED, reason=f"Hook corroboration search failed: {exc}"
        )
        return False

    job_dir = get_data_dir() / "jobs" / job.id / "research"
    _write_verification_log(job_dir, hook_log)

    # Pages found by claim searches become sources only if they actually supported a claim.
    used_ids = {e.source_id for a in assessments for e in a.evidence}
    for source_id, page in extra_pages.items():
        if source_id not in used_ids:
            continue
        file_path = job_dir / f"{source_id}.txt"
        file_path.write_text(page.source.text, encoding="utf-8")
        db.add(
            Source(
                id=source_id,
                job_id=job.id,
                url=page.url,
                domain=page.host,
                title=page.title,
                tier=page.tier,
                extracted_text_path=str(file_path),
            )
        )

    for assessment in assessments:
        candidate = assessment.candidate
        source_ids = [assessment.source.id] if assessment.source else []
        quote = candidate.supporting_quote
        match_score = assessment.quote_score
        if assessment.evidence:
            # Keep every supporting source and quote on the one claim so the audit trail is whole.
            source_ids += [e.source_id for e in assessment.evidence]
            quote = "\n---\n".join(
                [f"[{source_ids[0]}] {quote}"]
                + [f"[{e.source_id}] {e.quote}" for e in assessment.evidence]
            )
            match_score = min([match_score] + [e.quote_score for e in assessment.evidence])
        db.add(
            Claim(
                id=f"clm_{uuid.uuid4().hex[:8]}",
                job_id=job.id,
                source_ids=source_ids,
                text=candidate.text,
                supporting_quote=quote,
                quote_match_score=match_score,
                support_check=assessment.support_check,
                is_hook=assessment.hook_ok,
                status="verified" if assessment.verified else "rejected",
                reject_reason=assessment.reject_reason,
            )
        )
    db.commit()

    verified_claim_keys = {
        claim_key(assessment.candidate.text) for assessment in assessments if assessment.verified
    }
    min_claims = int(config.settings.get("verification", {}).get("min_verified_claims", 3))
    if len(verified_claim_keys) < min_claims or not verified_hook_keys:
        transition_job_state(
            db,
            job,
            JobState.DROPPED,
            reason="insufficient_verified_claims_or_hook_evidence",
        )
        return False

    return True
