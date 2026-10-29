import math
import re
import subprocess
from pathlib import Path

ANALYSIS_TIMEOUT_S = 120
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def luma_profile(path: Path) -> list[float]:
    """Average brightness (0-255) of each second of a clip; empty when it cannot be measured."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", path.name,
             "-vf", "fps=1,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
             "-an", "-f", "null", "-"],
            cwd=path.parent, capture_output=True, text=True, timeout=ANALYSIS_TIMEOUT_S,
            encoding="utf-8",
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return []
    if result.returncode != 0:
        return []
    return [float(value) for value in _YAVG.findall(result.stdout)]


def best_start(profile: list[float], needed_s: float, clip_s: float) -> tuple[float, float]:
    """Start (seconds) of the brightest window of ``needed_s`` and that window's mean brightness.

    Stock clips often open on a dark frame; a long clip is only used for a few seconds, so the
    brightest stretch is the better one to show. Short or unmeasured clips start at zero.
    """
    if not profile or clip_s <= needed_s:
        return 0.0, sum(profile) / len(profile) if profile else 0.0
    window = max(1, math.ceil(needed_s))
    last_start = max(0, min(len(profile) - window, int(clip_s - needed_s)))
    best = max(
        range(last_start + 1),
        key=lambda s: sum(profile[s : s + window]) / len(profile[s : s + window]),
    )
    chosen = profile[best : best + window]
    return float(best), sum(chosen) / len(chosen)


def gamma_for(mean_luma: float, target: float = 55.0, cap: float = 1.5) -> float:
    """Gamma that lifts a dark clip towards ``target`` mean brightness; 1.0 when bright enough."""
    if mean_luma <= 0 or mean_luma >= target:
        return 1.0
    mean = max(mean_luma, 5.0)
    return round(min(math.log(mean / 255) / math.log(target / 255), cap), 2)
