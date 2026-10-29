import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from shorts_engine.config import AppConfig, get_config
from shorts_engine.db.base import get_data_dir
from shorts_engine.db.models import Job, Script
from shorts_engine.domain.state_machine import JobState, transition_job_state
from shorts_engine.pipeline.forced_align import force_align
from shorts_engine.pipeline.subtitles import MIN_MATCH_SHARE, align_script, script_tokens
from shorts_engine.providers.asr.base import BaseASRProvider
from shorts_engine.providers.factory import get_asr_provider

logger = logging.getLogger(__name__)

FFPROBE_TIMEOUT_S = 30
FFMPEG_TIMEOUT_S = 180


class AudioError(RuntimeError):
    """A recording that cannot be used. The message is written for the person who recorded it."""

    def __init__(self, job_id: str, message: str):
        super().__init__(f"[{job_id}] audio: {message}")
        self.job_id = job_id
        self.stage = "audio"


@dataclass(frozen=True)
class AudioInfo:
    duration_s: float
    sample_rate: int
    channels: int


def _run(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
    )


def probe_audio(path: Path) -> AudioInfo:
    """Duration and format of the first audio stream; ValueError if there is none."""
    result = _run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels:format=duration",
            "-of", "json", path.name,
        ],
        path.parent,
        FFPROBE_TIMEOUT_S,
    )  # fmt: skip
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "ffprobe failed")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("no audio stream")
    return AudioInfo(
        duration_s=float(data["format"]["duration"]),
        sample_rate=int(streams[0]["sample_rate"]),
        channels=int(streams[0]["channels"]),
    )


def measure_loudness(path: Path) -> dict[str, float]:
    """Integrated loudness (LUFS) and true peak (dBTP) measured with the loudnorm filter."""
    result = _run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", path.name,
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        path.parent,
        FFMPEG_TIMEOUT_S,
    )  # fmt: skip
    start, end = result.stderr.rfind("{"), result.stderr.rfind("}")
    if result.returncode != 0 or start < 0 or end < start:
        raise ValueError("loudness measurement failed")
    data = json.loads(result.stderr[start : end + 1])
    return {"lufs": float(data["input_i"]), "true_peak_db": float(data["input_tp"])}


def build_trim_filter(silence_db: float) -> str:
    """Trim leading and trailing silence only; pauses inside the speech stay."""
    trim = f"silenceremove=start_periods=1:start_threshold={silence_db}dB:start_silence=0.15"
    # Mono first: loudness sums channel power, so a later downmix would lower it by about 3 LU.
    return f"aformat=channel_layouts=mono,{trim},areverse,{trim},areverse"


def _measure_after(trim: str, source: Path, target: float, peak: float) -> dict[str, str]:
    """First loudnorm pass: what the trimmed audio measures, as loudnorm's own JSON."""
    result = _run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", source.name,
         "-af", f"{trim},loudnorm=I={target}:TP={peak}:LRA=11:print_format=json",
         "-f", "null", "-"],
        source.parent,
        FFMPEG_TIMEOUT_S,
    )  # fmt: skip
    start, end = result.stderr.rfind("{"), result.stderr.rfind("}")
    if result.returncode != 0 or start < 0 or end < start:
        raise ValueError("loudness measurement failed")
    return json.loads(result.stderr[start : end + 1])


def build_filter(
    target_lufs: float, true_peak_db: float, silence_db: float, measured: dict[str, str]
) -> str:
    """Trim, then apply the second (linear) loudnorm pass with the values measured in the first."""
    return (
        f"{build_trim_filter(silence_db)},loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )


def prepare_narration(job_id: str, source: Path, out_dir: Path, config: AppConfig) -> Path:
    """Turn a recording into ``narration.wav`` (mono, 48 kHz, trimmed, normalised) or raise.

    Written to a temporary file and moved to the final name only after FFprobe accepts it.
    """
    settings = config.settings.get("audio", {})
    min_s = float(settings.get("min_duration_s", 20))
    max_s = float(settings.get("max_duration_s", 60))

    if not source.is_file():
        raise AudioError(job_id, "File rekaman tidak ditemukan.")
    try:
        info = probe_audio(source)
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        raise AudioError(job_id, f"File ini tidak berisi suara yang bisa dibaca ({exc}).") from exc
    if info.duration_s < min_s:
        raise AudioError(
            job_id, f"Rekaman terlalu pendek ({info.duration_s:.0f} detik, minimal {min_s:.0f})."
        )
    if info.duration_s > max_s + 10:
        raise AudioError(
            job_id, f"Rekaman terlalu panjang ({info.duration_s:.0f} detik, maksimal {max_s:.0f})."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    local_source = out_dir / f"original{source.suffix.lower()}"
    if local_source.resolve() != source.resolve():
        local_source.write_bytes(source.read_bytes())
    temp = out_dir / "narration.tmp.wav"
    final = out_dir / "narration.wav"
    target = float(settings.get("target_lufs", -16))
    peak = float(settings.get("true_peak_db", -1.5))
    silence_db = float(settings.get("silence_threshold_db", -45))
    try:
        measured = _measure_after(build_trim_filter(silence_db), local_source, target, peak)
        audio_filter = build_filter(target, peak, silence_db, measured)
    except (ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        raise AudioError(job_id, "Tidak ada suara yang cukup keras pada rekaman.") from exc
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", local_source.name,
        "-af", audio_filter,
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", temp.name,
    ]  # fmt: skip
    try:
        result = _run(args, out_dir, FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise AudioError(job_id, "Pemrosesan audio terlalu lama dan dihentikan.") from exc
    if result.returncode != 0:
        (out_dir / "ffmpeg_error.log").write_text(result.stderr, encoding="utf-8")
        raise AudioError(job_id, "Pemrosesan audio gagal (detail di ffmpeg_error.log).")
    try:
        probe_audio(temp)
    except (ValueError, subprocess.TimeoutExpired) as exc:
        raise AudioError(job_id, "Hasil pemrosesan audio tidak valid.") from exc
    temp.replace(final)
    return final


def extract_words(result: dict) -> list[dict]:
    return [
        {"word": word["word"], "start": word["start"], "end": word["end"]}
        for segment in result.get("segments", [])
        for word in segment.get("words", [])
    ]


def refine_with_script(
    db: Session, job: Job, narration: Path, words: list[dict]
) -> tuple[list[dict], str]:
    """Word times from the script text when the script is known; Whisper's otherwise.

    Whisper is first used to confirm the recording follows the script. The times then come from
    forced alignment of the script's own words, which is more exact, and the written words are
    the script's (digits stay digits). If alignment fails, Whisper's times are kept.
    """
    script = db.query(Script).filter(Script.job_id == job.id).first()
    if script is None:
        return words, "whisper"
    parts = [script.hook, *[segment["text"] for segment in script.segments], script.ending]
    share = align_script(script_tokens(parts), words)[1]
    if share < MIN_MATCH_SHARE:
        raise AudioError(
            job.id,
            f"Rekaman tidak cocok dengan naskah ({share:.0%} kata sama). "
            "Bacalah naskah apa adanya, lalu rekam ulang.",
        )
    try:
        forced = force_align(narration, parts)
    except Exception as exc:  # an enhancement: never lose the video because of it
        logger.warning("forced alignment skipped for %s: %s", job.id, exc)
        return words, "whisper"
    if any(word.end < word.start for word in forced):
        return words, "whisper"
    refined = [{"word": w.text, "start": round(w.start, 3), "end": round(w.end, 3)} for w in forced]
    return refined, "forced"


def run_audio_alignment(
    db: Session,
    job: Job,
    audio_file_path: str,
    asr: BaseASRProvider | None = None,
    config: AppConfig | None = None,
) -> bool:
    config = config or get_config()
    job_dir = get_data_dir() / "jobs" / job.id / "audio"
    transition_job_state(db, job, JobState.ALIGNING, reason="Menyiapkan audio dan waktu kata")
    try:
        narration = prepare_narration(job.id, Path(audio_file_path), job_dir, config)
        words = extract_words((asr or get_asr_provider()).transcribe(str(narration)))
        if not words:
            raise AudioError(job.id, "Tidak ada ucapan yang terdeteksi pada rekaman.")
        (job_dir / "alignment_whisper.json").write_text(
            json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        words, method = refine_with_script(db, job, narration, words)
        (job_dir / "alignment.json").write_text(
            json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (job_dir / "alignment_info.json").write_text(
            json.dumps({"method": method}), encoding="utf-8"
        )
    except AudioError as exc:
        transition_job_state(db, job, JobState.FAILED, reason=str(exc))
        return False
    except Exception as exc:
        logger.exception("audio stage failed for %s", job.id)
        transition_job_state(db, job, JobState.FAILED, reason=f"Gagal memproses audio: {exc}")
        return False
    transition_job_state(
        db, job, JobState.SELECTING_FOOTAGE, reason="Waktu kata selesai, lanjut pilih footage"
    )
    return True
