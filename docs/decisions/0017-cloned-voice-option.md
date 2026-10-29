# 0017 — Optional cloned voice for the narration

Status: accepted (2026-10-29). Deviates from SPEC.md, where the user records the narration; the
user approved it after listening to a test.

## Context

Reading every script aloud is tiring and the user reads English numbers with difficulty. A free
voice-cloning model was tested before any integration: Chatterbox (MIT code and model, checked
against the repository's LICENSE and the Hugging Face model card). On the RTX 3050 6 GB it used
3.87 GB of VRAM and generated 33 s of speech in 46 s. Whisper matched 97% of the script words
(the user's own recording: 92%). The user judged the voice acceptable.

## Decision

- `shorts finish JOB --clone` replaces the recording with speech synthesised from the script.
  Recording a file stays the default and still works (`shorts finish JOB file`).
- The model runs in a separate Python environment (`VOICE_PYTHON` in `.env`), through
  `tools/voice_worker.py`, because torch with CUDA is ~2.5 GB and Chatterbox pins torch 2.6. The
  project's dependencies only gained `num2words` (small, pure Python).
- Digits become words before synthesis (`pipeline/spoken.py`: years in pairs, grouped numbers as
  cardinals, ordinals, decimals, percent). Subtitles keep the script's digits.
- The reference voice is `data/voice/reference.wav` (not committed).
- Output carries Chatterbox's inaudible PerTh watermark.
- A job made with the clone gets a warning at the top of `description.txt` and in the command
  output: YouTube asks for a disclosure of realistic synthetic content when uploading. The
  sources found do not say whether a clone of one's own voice is covered; it is treated as covered.

## Consequences

- Cost per video for the voice: none; about one minute of GPU time after the first run (the first
  run downloads ~3 GB and took ~30 minutes on the user's connection).
- Quality is judged by the user only. The reference sample was a flat first recording, so the
  clone can sound flat; a livelier 20-30 s sample can be swapped in.
- Monetisation risk of AI narration is unquantified; a recorded voice stays the safer option.
