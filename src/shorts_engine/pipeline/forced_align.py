"""Word times from the script text itself (CTC forced alignment with wav2vec2 on onnxruntime).

Whisper guesses which words were said, and its word times drift. Here the words are known (the
script), so the model only has to find *when* each letter is spoken. Output words are the script's
own written words, so nothing here can mishear a number or a name.
"""

import json
import re
from pathlib import Path

import numpy as np

from shorts_engine.pipeline.spoken import spoken_words
from shorts_engine.pipeline.subtitles import TimedWord
from shorts_engine.providers.asr.whisper_provider import decode_audio

MODEL_REPO = "Xenova/wav2vec2-base-960h"  # Apache-2.0 wav2vec2 exported to ONNX
MODEL_FILE = "onnx/model_quantized.onnx"
VOCAB_FILE = "vocab.json"
FRAME_S = 0.02  # one model frame is 320 samples at 16 kHz
BLANK = 0  # "<pad>" is the CTC blank in this model
_LETTERS = re.compile(r"[^A-Z']")


class AlignmentError(RuntimeError):
    """Forced alignment could not be done (model missing, audio and text far apart)."""


def ctc_align(
    log_probs: np.ndarray, targets: list[int], blank: int = BLANK
) -> list[tuple[int, int]]:
    """Frame span (first, last) of every target token, by Viterbi over the CTC trellis.

    ``log_probs`` is (frames, vocabulary). Raises AlignmentError when the audio is too short for
    the text.
    """
    frames, _ = log_probs.shape
    length = len(targets)
    if length == 0:
        return []
    states = 2 * length + 1
    extended = np.full(states, blank, dtype=np.int64)
    extended[1::2] = targets
    if frames < length:
        raise AlignmentError("audio is shorter than its text")
    skip_ok = np.zeros(states, dtype=bool)  # a state may be entered from two states back
    skip_ok[3::2] = extended[3::2] != extended[1:-2:2]
    scores = np.full((frames, states), -np.inf)
    back = np.zeros((frames, states), dtype=np.int8)
    scores[0, 0] = log_probs[0, blank]
    scores[0, 1] = log_probs[0, extended[1]]
    for t in range(1, frames):
        previous = scores[t - 1]
        stay = previous
        one = np.concatenate(([-np.inf], previous[:-1]))
        two = np.concatenate(([-np.inf, -np.inf], previous[:-2]))
        two = np.where(skip_ok, two, -np.inf)
        choices = np.stack([stay, one, two])
        best = choices.argmax(axis=0)
        scores[t] = choices[best, np.arange(states)] + log_probs[t, extended]
        back[t] = best
    state = states - 1 if scores[-1, -1] >= scores[-1, -2] else states - 2
    if not np.isfinite(scores[-1, state]):
        raise AlignmentError("audio and text do not fit together")
    path = np.zeros(frames, dtype=np.int64)
    for t in range(frames - 1, -1, -1):
        path[t] = state
        state -= int(back[t, state])
    spans: list[tuple[int, int]] = []
    for token in range(length):
        inside = np.flatnonzero(path == 2 * token + 1)
        if len(inside) == 0:
            raise AlignmentError("a letter got no frames")
        spans.append((int(inside[0]), int(inside[-1])))
    return spans


def _label_ids(word: str, vocab: dict[str, int]) -> list[int]:
    letters = _LETTERS.sub("", word.upper())
    return [vocab[ch] for ch in letters if ch in vocab]


def _load_model():
    import onnxruntime
    from huggingface_hub import hf_hub_download

    try:
        model_path = hf_hub_download(MODEL_REPO, MODEL_FILE)
        vocab_path = hf_hub_download(MODEL_REPO, VOCAB_FILE)
    except Exception as exc:  # offline or blocked: the caller falls back to Whisper times
        raise AlignmentError("alignment model could not be downloaded") from exc
    session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    vocab = json.loads(Path(vocab_path).read_text(encoding="utf-8"))
    return session, vocab


def emissions(session, samples: np.ndarray) -> np.ndarray:
    """Log-probabilities per model frame (frames, vocabulary)."""
    normalised = (samples - samples.mean()) / np.sqrt(samples.var() + 1e-7)
    logits = session.run(
        None, {session.get_inputs()[0].name: normalised[None, :].astype(np.float32)}
    )[0][0]
    logits = logits - logits.max(axis=1, keepdims=True)
    return logits - np.log(np.exp(logits).sum(axis=1, keepdims=True))


def force_align(audio_path: Path, parts: list[str]) -> list[TimedWord]:
    """Every written word of ``parts`` with its start and end in the audio, in order."""
    session, vocab = _load_model()
    delimiter = vocab["|"]
    pieces: list[tuple[str, list[list[int]]]] = []  # written word, label ids of its spoken words
    for part in parts:
        for written, spoken in spoken_words(part):
            labels = [
                ids
                for word in spoken
                for sub in re.split(r"[-,]", word)
                if (ids := _label_ids(sub, vocab))
            ]
            pieces.append((written, labels))
    targets: list[int] = []
    owners: list[int] = []  # for each target token, which written word it belongs to
    for index, (_, labels) in enumerate(pieces):
        for ids in labels:
            if targets:
                targets.append(delimiter)
                owners.append(-1)
            targets += ids
            owners += [index] * len(ids)
    if not targets:
        raise AlignmentError("no letters to align")
    spans = ctc_align(emissions(session, decode_audio(audio_path)), targets)
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for (start, end), owner in zip(spans, owners, strict=True):
        if owner >= 0:
            first.setdefault(owner, start)
            last[owner] = end
    words = [
        TimedWord(written, first[i] * FRAME_S, (last[i] + 1) * FRAME_S) if i in first else None
        for i, (written, _) in enumerate(pieces)
    ]
    return _fill_gaps(words, [written for written, _ in pieces])


def _fill_gaps(words: list[TimedWord | None], written: list[str]) -> list[TimedWord]:
    """Written words with no letters to align (pure punctuation) share the time around them."""
    result: list[TimedWord] = []
    for index, word in enumerate(words):
        if word is not None:
            result.append(word)
            continue
        before = result[-1].end if result else 0.0
        result.append(TimedWord(written[index], before, before))
    return result
