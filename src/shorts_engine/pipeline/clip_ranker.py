"""Does a picture show what a shot asks for? CLIP (OpenAI, MIT) on onnxruntime, no torch.

Stock-site tags are written by uploaders and lie often (a fantasy planet tagged "jupiter", a
network globe tagged "planet"). CLIP looks at the picture itself: it scores how well an image
matches a sentence, and the score is turned into a probability against a list of things the
picture must *not* be (fiction, Earth, a city), so a clip is picked for what it shows.
"""

import subprocess

import numpy as np

REPO = "Xenova/clip-vit-base-patch32"
TEXT_MODEL = "onnx/text_model_quantized.onnx"
VISION_MODEL = "onnx/vision_model_quantized.onnx"
TOKENIZER = "tokenizer.json"
SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
LOGIT_SCALE = 100.0  # CLIP's learned temperature
DECODE_TIMEOUT_S = 30


class ClipError(RuntimeError):
    """The image model could not be loaded or an image could not be read."""


def preprocess(image_bytes: bytes) -> np.ndarray:
    """(3, 224, 224) float array: shorter side to 224, centre crop, CLIP's normalisation."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", "pipe:0",
             "-vf", f"scale={SIZE}:{SIZE}:force_original_aspect_ratio=increase:flags=bicubic,"
                    f"crop={SIZE}:{SIZE}",
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            input=image_bytes, capture_output=True, timeout=DECODE_TIMEOUT_S,
        )  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise ClipError("image decoding took too long") from exc
    if result.returncode != 0 or len(result.stdout) != SIZE * SIZE * 3:
        raise ClipError("image could not be read")
    pixels = np.frombuffer(result.stdout, dtype=np.uint8).reshape(SIZE, SIZE, 3)
    return ((pixels.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)


def softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


class ClipScorer:
    """Loads the two ONNX models on first use and scores images against sentences."""

    def __init__(self) -> None:
        self._text = None
        self._vision = None
        self._tokenizer = None
        self._cache: dict[str, np.ndarray] = {}

    def _load(self) -> None:
        if self._text is not None:
            return
        try:
            import onnxruntime
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            options = ["CPUExecutionProvider"]
            self._text = onnxruntime.InferenceSession(
                hf_hub_download(REPO, TEXT_MODEL), providers=options
            )
            self._vision = onnxruntime.InferenceSession(
                hf_hub_download(REPO, VISION_MODEL), providers=options
            )
            self._tokenizer = Tokenizer.from_file(hf_hub_download(REPO, TOKENIZER))
            self._tokenizer.no_padding()
            self._tokenizer.enable_truncation(max_length=77)
        except Exception as exc:  # offline, blocked or damaged download
            raise ClipError("image model could not be loaded") from exc

    def text_embeddings(self, texts: list[str]) -> np.ndarray:
        """Unit-length embeddings, one row per text (cached)."""
        self._load()
        rows = []
        for text in texts:
            if text not in self._cache:
                ids = np.array([self._tokenizer.encode(text).ids], dtype=np.int64)
                vector = self._text.run(None, {"input_ids": ids})[0][0]
                self._cache[text] = vector / np.linalg.norm(vector)
            rows.append(self._cache[text])
        return np.stack(rows)

    def image_embeddings(self, images: list[bytes]) -> np.ndarray:
        """Unit-length embeddings, one row per image; an unreadable image raises ClipError."""
        self._load()
        batch = np.stack([preprocess(data) for data in images]).astype(np.float32)
        vectors = self._vision.run(None, {"pixel_values": batch})[0]
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def match_probabilities(
        self, positive: str, negatives: list[str], images: list[bytes]
    ) -> list[float]:
        """For each image, the probability that ``positive`` describes it better than any negative."""
        texts = self.text_embeddings([positive, *negatives])
        pictures = self.image_embeddings(images)
        logits = LOGIT_SCALE * pictures @ texts.T
        return [float(p) for p in softmax(logits)[:, 0]]
