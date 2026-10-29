import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Job, Script, Topic
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.subtitles import align_script, script_tokens
from shorts_engine.providers.factory import get_footage_provider
from shorts_engine.providers.footage.base import (
    BaseFootageProvider,
    FootageCandidate,
    FootageProviderError,
)

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_S = 60.0
FFPROBE_TIMEOUT_S = 30
CANDIDATES_PER_QUERY = 20
TAIL_S = 0.3
FILLER_WORDS = frozenset(
    "a an and at by for from in into is it its of on or over seen the through to up with "
    "close slow slowly view shot shots footage video clip image showing shows".split()
)


class FootageError(RuntimeError):
    """A clip that cannot be fetched or used. Messages never contain URLs or keys."""


@dataclass
class Shot:
    index: int
    visual: str
    needed_s: float


@dataclass
class Pick:
    candidate: FootageCandidate
    query: str
    score: float
    loop: bool  # the clip is shorter than the shot and has to repeat


def content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in FILLER_WORDS]


def query_attempts(visual: str, topic_text: str, fallbacks: list[str]) -> list[str]:
    """Short queries from specific to general. Stock sites answer keywords, not sentences."""
    visual_words = content_words(visual)
    attempts = [
        " ".join(visual_words[:3]),
        " ".join(visual_words[:2]),
        " ".join(content_words(topic_text)[:2]),
        *fallbacks,
    ]
    unique: list[str] = []
    for query in attempts:
        if query and query not in unique:
            unique.append(query)
    return unique


def relevance(
    candidate: FootageCandidate,
    wanted: set[str],
    avoid: set[str],
    required: set[str] | None = None,
) -> float:
    """Tag overlap with the wanted words.

    A clip tagged with an avoided word is never used. When ``required`` is given, a clip must
    carry at least one of those words (the video's field), so that "storm clouds" cannot bring a
    tornado into a video about Jupiter.
    """
    tags = {word for tag in candidate.tags for word in content_words(tag)}
    if tags & avoid or (required and not tags & required):
        return -1.0
    return float(len(tags & wanted))


def shot_durations(parts: list[str], heard: list[dict]) -> list[float]:
    """Seconds each spoken part lasts. The hook belongs to the first shot, the ending to the last."""
    tokens = [script_tokens([part]) for part in parts]
    words, _ = align_script([t for group in tokens for t in group], heard)
    spans, cursor = [], 0
    for group in tokens:
        spans.append((words[cursor].start, words[cursor + len(group) - 1].end))
        cursor += len(group)
    return [
        (spans[i + 1][0] if i + 1 < len(spans) else spans[i][1] + TAIL_S)
        - (0.0 if i == 0 else spans[i][0])
        for i in range(len(spans))
    ]


def pick_footage(
    provider: BaseFootageProvider,
    shots: list[Shot],
    topic_text: str,
    settings: dict,
) -> list[Pick | None]:
    """Best clip per shot, never the same clip twice. A shot without a clip is None."""
    min_clip = float(settings.get("min_clip_seconds", 5))
    max_bytes = int(settings.get("max_clip_mb", 60)) * 1024 * 1024
    avoid = {w for tag in settings.get("avoid_tags", []) for w in content_words(tag)}
    required = {w for tag in settings.get("required_tags_any", []) for w in content_words(tag)}
    fallbacks = list(settings.get("fallback_queries", ["space", "stars"]))
    used: set[str] = set()
    picks: list[Pick | None] = []
    for shot in shots:
        wanted = set(content_words(shot.visual)) | set(content_words(topic_text))
        best: Pick | None = None
        for query in query_attempts(shot.visual, topic_text, fallbacks):
            for cand in provider.search_videos(query, per_page=CANDIDATES_PER_QUERY):
                key = f"{cand.provider}:{cand.asset_id}"
                if key in used or cand.duration_s < min_clip:
                    continue
                if cand.size_bytes and cand.size_bytes > max_bytes:
                    continue
                score = relevance(cand, wanted, avoid, required)
                if score < 0:
                    continue
                score += 0.5 if cand.duration_s >= shot.needed_s else 0.0
                if best is None or score > best.score:
                    best = Pick(cand, query, score, cand.duration_s < shot.needed_s)
            if best is not None and best.score >= 2:  # a good match; do not search wider
                break
        if best is not None:
            used.add(f"{best.candidate.provider}:{best.candidate.asset_id}")
        picks.append(best)
    return picks


def probe_video(path: Path) -> tuple[int, int, float]:
    """(width, height, duration) of the first video stream; ValueError when there is none."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", path.name],
        cwd=path.parent, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_S,
    )  # fmt: skip
    data = json.loads(result.stdout or "{}") if result.returncode == 0 else {}
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("no video stream")
    return int(streams[0]["width"]), int(streams[0]["height"]), float(data["format"]["duration"])


def download_clip(
    url: str,
    dest: Path,
    allowed_hosts: list[str],
    max_bytes: int,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Fetch a clip over HTTPS from an allowed host into a temp file, keep it only if it plays."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts
    ):
        raise FootageError("Alamat unduhan footage tidak diizinkan.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(".part")
    try:
        with httpx.Client(timeout=DOWNLOAD_TIMEOUT_S, transport=transport) as client:
            with client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise FootageError(f"Unduhan footage ditolak (HTTP {response.status_code}).")
                size = 0
                with temp.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        size += len(chunk)
                        if size > max_bytes:
                            raise FootageError("Berkas footage terlalu besar.")
                        handle.write(chunk)
        try:
            probe_video(temp)
        except (ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            raise FootageError("Berkas footage tidak bisa diputar.") from exc
        temp.replace(dest)
        return dest
    except httpx.TransportError as exc:
        raise FootageError(f"Unduhan footage gagal ({type(exc).__name__}).") from exc
    finally:
        temp.unlink(missing_ok=True)


def run_footage_selection(
    db: Session,
    job: Job,
    provider: BaseFootageProvider | None = None,
    config: AppConfig | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    config = config or get_config()
    settings = {**config.settings.get("topic_queue", {}), **config.settings.get("footage", {})}
    script = db.query(Script).filter(Script.job_id == job.id).first()
    topic = db.query(Topic).filter(Topic.id == job.topic_id).first()
    job_dir = get_data_dir() / "jobs" / job.id
    alignment_path = job_dir / "audio" / "alignment.json"
    if not script or not topic or not alignment_path.exists():
        transition_job_state(db, job, JobState.FAILED, reason="Naskah atau waktu kata tidak ada")
        return False

    provider = provider or get_footage_provider()
    parts = [script.hook, *[s["text"] for s in script.segments], script.ending]
    heard = json.loads(alignment_path.read_text(encoding="utf-8"))
    durations = shot_durations(parts, heard)
    # parts = hook + segments + ending; the hook and the ending ride on the neighbouring shots.
    shot_seconds = [durations[0] + durations[1], *durations[2:-2], durations[-2] + durations[-1]]
    if len(script.segments) == 1:
        shot_seconds = [sum(durations)]
    shots = [
        Shot(i, segment.get("visual_suggestion", ""), seconds)
        for i, (segment, seconds) in enumerate(zip(script.segments, shot_seconds, strict=False))
    ]
    try:
        picks = pick_footage(provider, shots, topic.text, settings)
    except FootageProviderError as exc:
        transition_job_state(db, job, JobState.FAILED, reason=f"Pencarian footage gagal: {exc}")
        return False
    missing = [shot.index + 1 for shot, pick in zip(shots, picks, strict=True) if pick is None]
    if missing:
        transition_job_state(
            db,
            job,
            JobState.DROPPED,
            reason=f"no_footage_for_segments:{','.join(map(str, missing))}",
        )
        return False

    hosts = list(settings.get("allowed_download_hosts", ["cdn.pixabay.com"]))
    max_bytes = int(settings.get("max_clip_mb", 60)) * 1024 * 1024
    plan = []
    try:
        for shot, pick in zip(shots, picks, strict=True):
            assert pick is not None
            cand = pick.candidate
            name = f"shot_{shot.index + 1}.mp4"
            path = download_clip(
                cand.download_url, job_dir / "footage" / name, hosts, max_bytes, transport
            )
            width, height, duration = probe_video(path)
            plan.append(
                {
                    "shot": shot.index + 1,
                    "file": f"footage/{name}",
                    "needed_s": round(shot.needed_s, 2),
                    "clip_s": round(duration, 2),
                    "loop": duration < shot.needed_s,
                    "width": width,
                    "height": height,
                    "query": pick.query,
                    "score": pick.score,
                    "provider": cand.provider,
                    "asset_id": cand.asset_id,
                    "page_url": cand.page_url,
                    "attribution": cand.attribution,
                    "tags": cand.tags,
                }
            )
    except FootageError as exc:
        transition_job_state(db, job, JobState.FAILED, reason=str(exc))
        return False

    (job_dir / "footage" / "footage_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    transition_job_state(db, job, JobState.RENDERING, reason="Footage siap, lanjut rendering")
    return True
