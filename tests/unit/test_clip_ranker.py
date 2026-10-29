import shutil
import subprocess

import numpy as np
import pytest

from shorts_engine.pipeline.clip_ranker import (
    REPO,
    TEXT_MODEL,
    VISION_MODEL,
    ClipError,
    ClipScorer,
    preprocess,
    softmax,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def solid(colour: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-f", "lavfi", "-i", f"color=c={colour}:s=320x180",
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        capture_output=True, check=True, timeout=30,
    ).stdout  # fmt: skip


def test_softmax_rows_sum_to_one_and_keep_the_order():
    out = softmax(np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0]]))

    assert out.sum(axis=1) == pytest.approx([1.0, 1.0])
    assert out[0, 2] > out[0, 1] > out[0, 0] and out[1, 0] == pytest.approx(1 / 3)


@needs_ffmpeg
def test_an_image_becomes_a_normalised_224_square():
    pixels = preprocess(solid("white"))

    assert pixels.shape == (3, 224, 224) and pixels.dtype == np.float32
    assert pixels[0, 0, 0] == pytest.approx((1.0 - 0.48145466) / 0.26862954, abs=0.05)


@needs_ffmpeg
def test_something_that_is_not_an_image_is_an_error():
    with pytest.raises(ClipError):
        preprocess(b"this is not a picture")


def _model_is_cached() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return all(
            isinstance(try_to_load_from_cache(REPO, name), str)
            for name in (TEXT_MODEL, VISION_MODEL, "tokenizer.json")
        )
    except Exception:
        return False


@needs_ffmpeg
@pytest.mark.skipif(not _model_is_cached(), reason="CLIP model not downloaded")
def test_the_real_model_tells_a_red_picture_from_a_blue_one():
    scorer = ClipScorer()

    red, blue = scorer.match_probabilities(
        "a plain red screen", ["a plain blue screen"], [solid("red"), solid("blue")]
    )

    assert red > 0.8 and blue < 0.2
