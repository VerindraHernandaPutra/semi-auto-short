# 0020 — Footage chosen by its picture, and an automatic quality gate

Status: accepted (2026-10-29)

## Context

Stock-site tags are written by uploaders and misled the footage filter repeatedly: a fantasy planet
tagged "jupiter", a network globe tagged "planet", a tornado for "storm clouds", a city for
"observatory". Each time a tag word was added to a deny-list, which does not scale.

## Decisions

- **Picture ranking** (`clip_ranker.py`): OpenAI CLIP ViT-B/32 (MIT; ONNX, quantised, ~154 MB,
  downloaded once) on onnxruntime, with no torch. A clip's preview image is scored against
  "a photo of <visual suggestion>" and against configurable negatives (fantasy illustration, globe of
  Earth, landscape, city, person, digital network). Tags only narrow the field and break ties.
  On the Jupiter shot, real Jupiter clips scored 0.64-0.91 and the tornado, ocean and fantasy-planet
  clips 0.02-0.08.
- If the model judges every candidate poorly (< `clip.weak_probability`), the shot gets no clip and
  the topic is dropped: no clip beats a wrong one. If the model cannot be used at all (offline,
  broken download), tags decide as before.
- Previews are fetched over HTTPS from the allow-listed host only, without redirects, capped at 3 MB.
- **Vertical clips** fill the whole frame (no blurred copy). A render failed on the first portrait
  clip because the square crop assumed landscape; the crop now scales to cover first.
- **Quality gate** (`quality.py`, job state QUALITY_CHECK): volume and true peak, subtitle sync
  (median gap between real speech starts in the audio and subtitle starts, limit 0.12 s), a quiet
  ending, black picture, frozen picture, dark shots, weak footage matches, and duration. Notes are
  plain Indonesian, written to `render/quality.json` and printed by `finish`; a check that cannot
  be measured is shown as such, never as passed. The gate informs the person watching; it does not
  stop the video.

## Verified

- Gate sensitivity: shifting the subtitles by 0.15 s gives a median error of 0.17 s (flagged); the
  forced alignment measures 0.02 s, the older Whisper alignment 0.07 s.
- Jupiter run: all five shots matched at 0.97-1.0, 9 of 9 checks good.

## Not verified

- CLIP rates a preview frame, which can differ from the rest of the clip. It cannot tell AI-made
  art from footage (the swirling nebula on shot 5 looks like AI art and carries no "ai generated"
  tag). Subjective quality stays with the person watching.
