import uuid

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.models import Claim, Job, Script
from shorts_engine.domain.schemas import ScriptClaimCheck, ScriptOutput
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.claim_validation import numeric_facts_are_supported
from shorts_engine.providers.factory import get_llm_provider, is_mock_provider
from shorts_engine.providers.llm.base import BaseLLMProvider, LLMProviderError
from shorts_engine.services.costs import check_budget, track_costs


def _claim_text_for_ids(claim_ids: list[str], claims_by_id: dict[str, Claim]) -> str:
    return "\n".join(f"[{claim_id}] {claims_by_id[claim_id].text}" for claim_id in claim_ids)


def _validate_references(script_data: ScriptOutput, claims_by_id: dict[str, Claim]):
    all_claim_ids = set(script_data.hook_claim_ids)
    all_claim_ids.update(script_data.ending_claim_ids)
    for segment in script_data.segments:
        all_claim_ids.update(segment.claim_ids)
    unknown_claim_ids = all_claim_ids - claims_by_id.keys()
    if unknown_claim_ids:
        unknown_ids = ", ".join(sorted(unknown_claim_ids))
        return f"Script references unverified claims: {unknown_ids}"

    if not script_data.hook_claim_ids:
        return "Hook has no verified claim references"

    referenced_text = _claim_text_for_ids(script_data.hook_claim_ids, claims_by_id)
    if not numeric_facts_are_supported(script_data.hook, referenced_text):
        return "Hook contains a number or unit absent from its verified claims"

    for segment in script_data.segments:
        if segment.kind == "factual" and not segment.claim_ids:
            return "Factual segment has no verified claim references"
        if segment.kind == "framing" and segment.claim_ids:
            return "Framing segment must not reference factual claims"
        if not numeric_facts_are_supported(
            segment.text, _claim_text_for_ids(segment.claim_ids, claims_by_id)
        ):
            return "Segment contains a number or unit absent from its verified claims"

    if script_data.ending_kind == "factual" and not script_data.ending_claim_ids:
        return "Factual ending has no verified claim references"
    if script_data.ending_kind == "framing" and script_data.ending_claim_ids:
        return "Framing ending must not reference factual claims"
    if not numeric_facts_are_supported(
        script_data.ending,
        _claim_text_for_ids(script_data.ending_claim_ids, claims_by_id),
    ):
        return "Ending contains a number or unit absent from its verified claims"

    return None


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


def _write_script(db: Session, job: Job, llm: BaseLLMProvider, config: AppConfig) -> bool:
    # Ambil hanya klaim yang lolos verifikasi
    verified_claims = (
        db.query(Claim).filter(Claim.job_id == job.id, Claim.status == "verified").all()
    )

    if not verified_claims:
        transition_job_state(
            db, job, JobState.DROPPED, reason="Tidak ada klaim verified untuk naskah"
        )
        return False

    transition_job_state(db, job, JobState.SCRIPTING, reason="Mulai penulisan naskah")

    claims_text = "\n".join([f"- [{c.id}] {c.text}" for c in verified_claims])

    prompt = f"""
    Tulis naskah video pendek sains (Shorts) berdasarkan fakta berikut HANYA. 
    Setiap kalimat faktual, termasuk hook, harus mencantumkan claim_id yang mendukungnya.
    Tandai kalimat tanpa fakta sebagai framing tanpa claim_id.
    Fakta:
    {claims_text}
    """

    model_heavy = config.settings.get("models", {}).get("heavy", "claude-sonnet-5-5")

    try:
        script_data = llm.generate_structured(prompt, ScriptOutput, model_heavy)
    except LLMProviderError as exc:
        transition_job_state(db, job, JobState.FAILED, reason=f"Script generation failed: {exc}")
        return False

    claims_by_id = {claim.id: claim for claim in verified_claims}
    reference_error = _validate_references(script_data, claims_by_id)
    if reference_error:
        transition_job_state(
            db, job, JobState.FAILED, reason=f"Naskah gagal validasi: {reference_error}"
        )
        return False

    min_duration = int(config.settings.get("speech", {}).get("target_duration_min", 35))
    max_duration = int(config.settings.get("speech", {}).get("target_duration_max", 55))
    if not min_duration <= script_data.estimated_duration_s <= max_duration:
        transition_job_state(
            db,
            job,
            JobState.FAILED,
            reason="Naskah gagal validasi: estimasi durasi di luar target",
        )
        return False

    model_light = config.settings.get("models", {}).get("light", "claude-haiku-5-5")
    statements = [
        (script_data.hook, script_data.hook_claim_ids),
        *[(segment.text, segment.claim_ids) for segment in script_data.segments],
    ]
    statements.append((script_data.ending, script_data.ending_claim_ids))
    statements.append((script_data.title_working, list(claims_by_id)))
    script_checks = []
    for statement, claim_ids in statements:
        prompt_check = (
            "Check whether this script statement is fully and accurately supported "
            "by the verified claims. Flag added specificity, unsupported details, "
            "numbers, units, or species names. Use consistent only if every factual "
            "part is supported. If no verified claims are provided, the statement "
            "must be nonfactual framing; otherwise mark it unsupported.\n"
            f"Statement: {statement}\n"
            f"Verified claims:\n{_claim_text_for_ids(claim_ids, claims_by_id)}"
        )
        try:
            check = llm.generate_structured(prompt_check, ScriptClaimCheck, model_light)
        except LLMProviderError as exc:
            transition_job_state(db, job, JobState.FAILED, reason=f"Script check failed: {exc}")
            return False
        if check.status != "consistent":
            transition_job_state(
                db,
                job,
                JobState.FAILED,
                reason=f"Naskah tidak didukung klaim: {check.reason}",
            )
            return False
        script_checks.append(
            {
                "statement": statement,
                "claim_ids": claim_ids,
                "status": check.status,
                "reason": check.reason,
            }
        )

    segments_json = [s.model_dump() for s in script_data.segments]

    new_script = Script(
        id=f"scr_{uuid.uuid4().hex[:8]}",
        job_id=job.id,
        version=1,
        hook=script_data.hook,
        segments=segments_json,
        ending=script_data.ending,
        structure=script_data.story_structure,
        check_results={
            "hook_claim_ids": script_data.hook_claim_ids,
            "ending_kind": script_data.ending_kind,
            "ending_claim_ids": script_data.ending_claim_ids,
            "title_claim_ids": list(claims_by_id),
            "script_claim_checks": script_checks,
        },
        status="passed",
    )
    db.add(new_script)
    db.commit()

    # Naskah siap direkam oleh pengguna
    transition_job_state(db, job, JobState.AWAITING_VOICE, reason="Naskah selesai dan siap direkam")
    return True
