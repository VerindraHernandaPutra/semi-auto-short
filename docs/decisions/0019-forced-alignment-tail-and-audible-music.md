# 0019 — Forced alignment for subtitles, a quiet ending, and an audible music bed

Status: accepted (2026-10-29)

## Context

The user reported that subtitles did not follow the voice, the video ended abruptly, and the music
could not be heard.

## Findings (measured, not guessed)

- **Stale subtitles (a bug of ours).** `run_rendering` only built `subtitles.ass` when none existed.
  After the clone run, the file made from the user's slower recording was reused with the clone
  audio, so captions fell further behind towards the end (a sentence from 4.5 s earlier was still
  on screen at 33.7 s). Subtitles are now rebuilt on every render.
- **Whisper word times are loose.** Against the speech energy of the clone audio, the mean error at
  the start of a speech stretch was 0.079 s (worst 0.29 s); on the user's recording 0.31 s (worst
  0.48 s). Whisper also mishears numbers and names.
- **Karaoke slots.** `\k` highlights a word at the start of its slot. Slots were built from word
  ends, so a pause before a word moved its highlight early. Slots now run from a word's start to
  the next word's start.
- **Music was inaudible**: notes at 110-330 Hz (phone speakers barely play below ~200 Hz), the bed
  was -21.8 LUFS before an extra -22 dB, and heavy ducking. In the pauses it measured about -46 dBFS.
- **The video stopped on the last word**, with no tail.
- `container` offsets were checked and are not the cause (audio and video both start at 0.000).

## Decisions

- **Forced alignment** (`forced_align.py`): wav2vec2-base-960h (Apache-2.0, ONNX, 95 MB) with
  onnxruntime and a numpy CTC Viterbi, no torch. It aligns the script's own words (numbers
  expanded with `spoken_words`), so written words stay the script's. Result on the clone audio:
  mean start error 0.033 s (worst 0.07 s). Whisper still checks that the recording follows the
  script (at least 75% of words) and is the fallback when the model cannot be loaded.
  The alignment file records its method (`alignment_info.json`).
- **Ending:** `render.tail_s` = 1.2 s of quiet after the last word (the last shot is extended, the
  narration padded with silence) and everything fades out over `fade_out_s` = 0.8 s.
- **Music:** the pad sits at 220-660 Hz, is loudness-normalised to -23 LUFS (`music.target_lufs`),
  and ducks gently (ratio 4, threshold 0.1). Result: -22 to -26 dBFS in the pauses, about 10 dB below
  the voice. The limiter keeps auto-level off.

## Not verified

- Alignment accuracy inside a sentence has no ground truth here; the figures above are speech starts
  after pauses and speech coverage. Playback smoothness and the taste of the music remain with the user.
