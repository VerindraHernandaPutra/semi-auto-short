import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig
from shorts_engine.db.models import Claim, Job, Script, Source
from shorts_engine.pipeline.claim_validation import claim_key
from shorts_engine.pipeline.script_rules import spoken_text

MAX_TITLE_CHARS = 60


def cited_claim_ids(script: Script) -> list[str]:
    """Every claim the script leans on, in order of first use."""
    results = script.check_results or {}
    ids = list(results.get("hook_claim_ids", []))
    for segment in script.segments:
        ids += segment.get("claim_ids", [])
    ids += results.get("ending_claim_ids", [])
    return list(dict.fromkeys(ids))


def clean_title(title: str, series: str | None) -> str:
    """The title without the series label or episode number the model may have put into it.

    The label is added once, at the end, by ``build_title``; "The Moon Drifts: Everyone Gets This
    Wrong" would otherwise show the label twice.
    """
    text = " ".join(title.split())
    if series:
        text = re.sub(re.escape(series), " ", text, flags=re.IGNORECASE)
    text = re.sub(r"#\s*\d+", " ", text)
    return " ".join(text.split()).strip(" :-–—|,")


def video_title(script: Script) -> str:
    """The plain working title of a script, as shown over the video."""
    results = script.check_results or {}
    return clean_title(str(results.get("title_working", "")), results.get("series"))


def build_title(script: Script) -> str:
    """Plain title with the series label at the end when it still fits (style guide)."""
    results = script.check_results or {}
    title = video_title(script)
    series, episode = results.get("series"), results.get("episode_number")
    label = f"{series} #{episode}" if series and episode else ""
    if label and len(f"{title} | {label}") <= MAX_TITLE_CHARS:
        return f"{title} | {label}"
    return title[:MAX_TITLE_CHARS].rstrip()


def source_lines(db: Session, job: Job, script: Script) -> list[str]:
    """Title and URL of every source behind a cited claim. URLs come from the database only.

    A claim confirmed by several independent sources (the hook) exists once per source; the script
    cites one of them, but every source that states the same claim is credited.
    """
    cited = cited_claim_ids(script)
    verified = db.query(Claim).filter(Claim.job_id == job.id, Claim.status == "verified").all()
    cited_keys = {claim_key(c.text) for c in verified if c.id in cited}
    claims = [c for c in verified if c.id in cited or claim_key(c.text) in cited_keys]
    claims.sort(key=lambda c: (c.id not in cited, c.id))  # the cited claims' sources come first
    source_ids = list(dict.fromkeys(sid for claim in claims for sid in (claim.source_ids or [])))
    sources = {
        s.id: s for s in db.query(Source).filter(Source.job_id == job.id, Source.id.in_(source_ids))
    }
    lines = []
    for source_id in source_ids:
        source = sources.get(source_id)
        if source is not None:
            lines.append(f"- {source.title or source.domain}: {source.url}")
    return lines


def footage_lines(job_dir: Path) -> list[str]:
    plan_file = job_dir / "footage" / "footage_plan.json"
    plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.is_file() else []
    return [f"- {a}" for a in dict.fromkeys(item["attribution"] for item in plan)]


def build_description(db: Session, job: Job, job_dir: Path, config: AppConfig) -> str:
    """Two-sentence summary, sources, footage credits, a note on the visuals and hashtags."""
    script = db.query(Script).filter(Script.job_id == job.id).one()
    settings = config.settings.get("description", {})
    summary = " ".join(
        spoken_text(text)
        for text in (script.hook, script.segments[0]["text"] if script.segments else "")
        if text
    )
    parts = [summary, "Sources:\n" + "\n".join(source_lines(db, job, script))]
    credits = footage_lines(job_dir)
    footage = "Footage:\n" + "\n".join(credits) if credits else ""
    note = settings.get("visuals_note", "")
    parts.append("\n".join(filter(None, [footage, note])))
    parts.append(" ".join(settings.get("hashtags", ["#Shorts"])))
    return "\n\n".join(part for part in parts if part.strip()) + "\n"


def write_description(db: Session, job: Job, job_dir: Path, config: AppConfig) -> Path:
    script = db.query(Script).filter(Script.job_id == job.id).one()
    path = job_dir / "render" / "description.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"Title: {build_title(script)}\n\n{build_description(db, job, job_dir, config)}"
    source_file = job_dir / "audio" / "voice_source.txt"
    if source_file.is_file() and source_file.read_text(encoding="utf-8").strip() == "clone":
        note = (
            "[CATATAN UNTUK ANDA, JANGAN DISALIN KE YOUTUBE: suara video ini dibuat komputer "
            "(klon suara Anda). Saat mengunggah, centang pilihan konten yang diubah atau "
            "sintetis di YouTube Studio.]"
        )
        text = f"{note}\n\n{text}"
    path.write_text(text, encoding="utf-8")
    return path
