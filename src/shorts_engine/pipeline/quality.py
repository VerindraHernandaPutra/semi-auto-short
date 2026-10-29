"""Automatic checks of a finished video, so the person who watches it can trust what they see.

Everything here is measured locally (FFmpeg and numpy), nothing is paid for. A check that cannot
be measured is reported as such; it never counts as passed.
"""

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from shorts_engine.config import AppConfig
from shorts_engine.pipeline.spoken import is_abbreviation, spoken_words, to_spoken
from shorts_engine.pipeline.subtitles import _norm
from shorts_engine.providers.asr.whisper_provider import decode_audio

HOP_S = 0.01
FFMPEG_TIMEOUT_S = 180
_BLACK = re.compile(r"black_duration:([0-9.]+)")
_FREEZE = re.compile(r"freeze_duration:\s*([0-9.]+)")


@dataclass
class Check:
    name: str
    status: str  # "ok", "perhatian" (look at it) or "tidak terukur"
    message: str  # plain Indonesian for the person watching


def envelope_db(samples: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Loudness (dB) of every 10 ms of audio."""
    step = int(sample_rate * HOP_S)
    frames = len(samples) // step
    blocks = samples[: frames * step].reshape(frames, step)
    return 20 * np.log10(np.sqrt((blocks**2).mean(axis=1)) + 1e-12)


def speech_starts(
    envelope: np.ndarray, drop_db: float = 28.0, quiet_s: float = 0.22
) -> list[float]:
    """Moments (seconds) at which speech begins after at least ``quiet_s`` of quiet.

    Speech is anything within ``drop_db`` of the loudest frame. These starts are ground truth for
    word timing because a start after a pause is clear in the audio itself.
    """
    active = envelope > envelope.max() - drop_db
    starts: list[float] = []
    quiet = int(quiet_s / HOP_S)
    silent_for = quiet  # the clip may open with speech
    for index, is_speech in enumerate(active):
        if is_speech and silent_for >= quiet:
            starts.append(index * HOP_S)
        silent_for = 0 if is_speech else silent_for + 1
    return starts


def sync_error(word_starts: list[float], speech: list[float]) -> float | None:
    """Median seconds between each real speech start and the nearest word start; None if unmeasurable."""
    usable = [t for t in speech if t > 0.3]  # the very first start is cut by silence removal
    if len(usable) < 3 or not word_starts:
        return None
    return float(np.median([min(abs(w - t) for w in word_starts) for t in usable]))


def unclear_sentences(
    parts: list[str], heard: list[dict], max_missing_share: float = 0.4
) -> list[str]:
    """Sentences of the script that speech recognition did not hear well.

    Forced alignment always places every word, so it cannot show a word the voice garbled or left
    out; Whisper's own transcript can. A written word counts as heard when it matches Whisper in
    its written form ("2017") or in its spoken form ("twenty seventeen"). A sentence is flagged
    when more than ``max_missing_share`` of its words were not heard, or its last two were not.
    """
    pairs = [
        (written, spoken)
        for part in parts
        for written, spoken in spoken_words(part)
        if _norm(written)
    ]
    heard_words = [w["word"] for w in heard if _norm(w["word"])]
    matched = [False] * len(pairs)

    def mark(wanted: list[str], owners: list[int], got: list[str]) -> None:
        for tag, i1, i2, _j1, _j2 in SequenceMatcher(
            None, wanted, got, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                for i in range(i1, i2):
                    matched[owners[i]] = True

    mark([_norm(w) for w, _ in pairs], list(range(len(pairs))), [_norm(w) for w in heard_words])
    spoken = [
        (i, _norm(word)) for i, (_, words) in enumerate(pairs) for word in words if _norm(word)
    ]
    heard_spoken = [_norm(w) for w in to_spoken(" ".join(heard_words)).split() if _norm(w)]
    mark([word for _, word in spoken], [i for i, _ in spoken], heard_spoken)

    flagged: list[str] = []
    sentence: list[int] = []
    for index, (written, _) in enumerate(pairs):
        sentence.append(index)
        if (
            written.endswith((".", "!", "?"))
            and not is_abbreviation(written)
            or index == len(pairs) - 1
        ):
            missing = [not matched[i] for i in sentence]
            tail_lost = len(sentence) >= 4 and missing[-1] and missing[-2]
            if sum(missing) / len(sentence) > max_missing_share or tail_lost:
                text = " ".join(pairs[i][0] for i in sentence)
                flagged.append(text if len(text) <= 80 else text[:77] + "...")
            sentence = []
    return flagged


def _ffmpeg_log(video: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", video.name, *args, "-f", "null", "-"],
        cwd=video.parent, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S, encoding="utf-8",
    )  # fmt: skip
    return result.stderr


def black_seconds(video: Path, until_s: float) -> float:
    """Seconds of (near) black picture before ``until_s``; the fade-out at the end is not counted."""
    log = _ffmpeg_log(
        video, ["-t", f"{until_s:.3f}", "-vf", "blackdetect=d=0.4:pix_th=0.08", "-an"]
    )
    return sum(float(m) for m in _BLACK.findall(log))


def longest_freeze(video: Path, until_s: float) -> float:
    """Longest stretch of identical frames before ``until_s``."""
    log = _ffmpeg_log(video, ["-t", f"{until_s:.3f}", "-vf", "freezedetect=n=0.003:d=1.5", "-an"])
    found = [float(m) for m in _FREEZE.findall(log)]
    return max(found, default=0.0)


def run_quality_checks(
    job_dir: Path,
    report: dict,
    narration_s: float,
    config: AppConfig,
    parts: list[str] | None = None,
) -> list[Check]:
    """Checks of ``render/final.mp4`` and the files it was made from; also saved as quality.json."""
    final = job_dir / "render" / "final.mp4"
    settings = config.settings.get("quality", {})
    checks: list[Check] = []

    def add(name: str, ok: bool | None, good: str, bad: str) -> None:
        status = "tidak terukur" if ok is None else ("ok" if ok else "perhatian")
        checks.append(Check(name, status, good if ok else bad))

    lufs, peak = report.get("lufs"), report.get("true_peak_db")
    lo, hi = float(settings.get("lufs_min", -17.5)), float(settings.get("lufs_max", -14.5))
    add("volume", None if lufs is None else lo <= lufs <= hi,
        f"Volume pas ({lufs} LUFS).", f"Volume kurang pas ({lufs} LUFS, seharusnya {lo} sampai {hi}).")  # fmt: skip
    add("puncak suara", None if peak is None else peak <= -1.0,
        "Suara tidak pecah.", f"Puncak suara terlalu tinggi ({peak} dB), bisa terdengar pecah.")  # fmt: skip

    try:
        narration = decode_audio(job_dir / "audio" / "narration.wav")
        words = json.loads((job_dir / "audio" / "alignment.json").read_text(encoding="utf-8"))
        offset = sync_error([w["start"] for w in words], speech_starts(envelope_db(narration)))
    except (OSError, ValueError, RuntimeError, KeyError):
        offset = None
    limit = float(settings.get("max_sync_error_s", 0.12))
    if offset is None:
        checks.append(
            Check(
                "subtitle selaras",
                "tidak terukur",
                "Selaras tidaknya subtitle tidak bisa diukur (suara tanpa jeda yang jelas).",
            )
        )
    else:
        add(
            "subtitle selaras",
            offset <= limit,
            f"Subtitle selaras dengan suara (selisih tengah {offset:.2f} detik).",
            f"Subtitle kurang selaras dengan suara (selisih tengah {offset:.2f} detik).",
        )

    try:
        tail = envelope_db(decode_audio(final))[-20:]
        add("akhir video", float(tail.mean()) < -45,
            "Video diakhiri dengan hening yang halus.", "Akhir video masih berisik, bisa terasa terpotong.")  # fmt: skip
        body = max(float(report.get("duration_s", narration_s)) - 1.0, 1.0)
        dark = black_seconds(final, body)
        add("gambar hitam", dark <= float(settings.get("max_black_s", 1.0)),
            "Tidak ada gambar hitam di tengah video.", f"Ada gambar hitam selama {dark:.1f} detik di dalam video.")  # fmt: skip
        frozen = longest_freeze(final, body)
        add("gambar diam", frozen <= float(settings.get("max_freeze_s", 3.0)),
            "Gambar terus bergerak.", f"Ada gambar yang diam sampai {frozen:.1f} detik.")  # fmt: skip
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        checks.append(Check("video", "tidak terukur", "Video hasil tidak bisa diperiksa."))

    if parts:
        heard_file = job_dir / "audio" / "alignment_whisper.json"
        if not heard_file.is_file():
            heard_file = job_dir / "audio" / "alignment.json"
        try:
            heard = json.loads(heard_file.read_text(encoding="utf-8"))
            unclear = unclear_sentences(parts, heard)
        except (OSError, ValueError):
            unclear = None
        if unclear is None:
            checks.append(
                Check(
                    "kalimat terdengar", "tidak terukur", "Kejelasan ucapan tidak bisa diperiksa."
                )
            )
        else:
            quoted = "; ".join(f'"{sentence}"' for sentence in unclear[:3])
            add(
                "kalimat terdengar",
                not unclear,
                "Setiap kalimat terdengar lengkap.",
                f"Kalimat ini kurang jelas atau terpotong, dengarkan bagian ini: {quoted}",
            )

    look_file = job_dir / "render" / "look.json"
    plan_file = job_dir / "footage" / "footage_plan.json"
    if look_file.is_file():
        looks = json.loads(look_file.read_text(encoding="utf-8"))
        dim = [
            item["shot"]
            for item in looks
            if item["mean_luma"] < float(settings.get("min_luma", 25))
        ]
        add("kecerahan", not dim, "Semua shot cukup terang.",
            f"Shot {', '.join(map(str, dim))} terlalu gelap.")  # fmt: skip
    if plan_file.is_file():
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        floor = float(settings.get("min_clip_probability", 0.5))
        weak = [
            p["shot"]
            for p in plan
            if p.get("clip_probability") is not None and p["clip_probability"] < floor
        ]
        judged = any(p.get("clip_probability") is not None for p in plan)
        add("kecocokan footage", (not weak) if judged else None,
            "Semua footage cocok dengan isi kalimatnya.",
            f"Footage shot {', '.join(map(str, weak))} kurang cocok dengan kalimatnya." if weak
            else "Kecocokan footage tidak bisa dinilai.")  # fmt: skip
    duration = float(report.get("duration_s", 0))
    add("durasi", 20 <= duration <= 59, f"Durasi {duration:.0f} detik, cocok untuk Shorts.",
        f"Durasi {duration:.0f} detik di luar 20-59 detik.")  # fmt: skip

    (job_dir / "render" / "quality.json").write_text(
        json.dumps([asdict(check) for check in checks], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return checks


def attention_notes(checks: list[Check]) -> list[str]:
    """Messages of the checks that need a look, for the person watching."""
    return [check.message for check in checks if check.status != "ok"]
