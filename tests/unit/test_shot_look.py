import shutil
import subprocess

import pytest

from shorts_engine.pipeline.shot_look import best_start, gamma_for, luma_profile

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def test_a_long_clip_starts_at_its_brightest_stretch():
    profile = [10, 10, 10, 80, 90, 85, 20, 10, 10, 10]

    start, mean = best_start(profile, needed_s=3.0, clip_s=10.0)

    assert start == 3.0 and mean == pytest.approx(85.0)


def test_a_clip_no_longer_than_the_shot_starts_at_zero():
    assert best_start([5, 90, 90], needed_s=3.0, clip_s=3.0)[0] == 0.0
    assert best_start([], needed_s=3.0, clip_s=30.0) == (0.0, 0.0)


def test_the_window_never_runs_past_the_end_of_the_clip():
    start, _ = best_start([10, 10, 10, 10, 200], needed_s=3.0, clip_s=5.0)

    assert start + 3 <= 5


@pytest.mark.parametrize(
    ("mean", "expected"),
    [(80.0, 1.0), (55.0, 1.0), (30.0, 1.4), (1.0, 1.5), (0.0, 1.0)],
)
def test_dark_clips_get_a_gamma_lift_up_to_the_cap(mean, expected):
    assert gamma_for(mean) == pytest.approx(expected, abs=0.05)


@needs_ffmpeg
def test_measured_brightness_follows_the_picture(tmp_path):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=10:d=3",
            "-f", "lavfi", "-i", "color=c=white:s=160x90:r=10:d=3",
            "-filter_complex", "[0][1]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", "c.mp4",
        ],
        cwd=tmp_path, check=True, capture_output=True, timeout=60,
    )  # fmt: skip

    profile = luma_profile(tmp_path / "c.mp4")

    assert len(profile) >= 5
    assert profile[0] < 30 and profile[-1] > 200
    assert best_start(profile, 2.0, 6.0)[0] >= 3.0  # the white half
