# 0016 — Media stages of the vertical slice: audio, subtitles, footage, render

Status: accepted (2026-10-29). Footage source beyond Pixabay is still open (see below).

## Decisions

- **Whisper:** `openai-whisper` (pulls CPU torch) is replaced by `faster-whisper` with the pip
  NVIDIA libraries (cuBLAS, cuDNN). The user approved GPU use. The device falls back to CPU if CUDA
  cannot load. Audio is decoded with FFmpeg because PyAV 19 breaks faster-whisper 1.2.1's own
  decoding call.
- **Audio:** mono first, then trim leading/trailing silence only, then two-pass `loudnorm`
  (-16 LUFS, -1.5 dBTP). Loudness sums channel power, so a downmix after normalising lowered it
  by 3 LU. Pass 1 measures, pass 2 applies linearly.
- **Subtitles:** the text shown is the script text; Whisper only supplies times. Words heard as
  written keep their own times; misheard or skipped words share the gap. A recording matching
  under 75% of the script is refused. Font: Poppins Bold (OFL) in `assets/fonts/`.
- **Footage:** short keyword queries, tag score, no clip twice, fiction tags rejected, and a clip
  must carry a field tag (`required_tags_any`) so generic words cannot bring in unrelated footage
  (a tornado for Jupiter). Downloads: HTTPS, host allowlist, no redirects, 60 MB cap, `.part`
  file kept only if FFprobe accepts it. No usable clip for a shot drops the topic.
- **Render:** each shot is composed 9:16 (landscape clip over a blurred, enlarged copy), the shots
  are concatenated, then narration and burned-in subtitles are added in one final encode. Output is
  checked (1080x1920, has audio, duration within 0.6 s of the narration). libx264 is the default;
  `render.encoder: h264_nvenc` is available.

## Measured

- Real Jupiter run (TTS narration, 5 real Pixabay clips): 40 s video rendered in 17 s on CPU.
- Pixabay space footage is almost all landscape, HD, and CGI. Tags are uploader-written and can
  mislead (a fantasy ringed planet tagged "jupiter").

## Open

- NASA Image and Video Library (real footage; free; credit NASA, no implied endorsement, logo
  clearance) would deviate from SPEC.md's footage providers. Waiting for the user.
- Quality of the audio chain with the user's real voice is untested (TTS only).
