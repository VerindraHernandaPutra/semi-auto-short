# 0018 — Video polish: data bars, title card, branding, music bed, cross-fades

Status: accepted (2026-10-29)

## Decisions

- **Look:** a 1080x1080 centre crop of the clip over its blurred copy; each shot starts at the
  brightest stretch of its clip, dark clips get a gamma lift (cap 1.5), a slow 7% push-in.
- **Data bars** (`data_graphics.py`): a part with exactly two amounts in the same unit (units are
  configured) is drawn as two bars that grow when each number is spoken. Only numbers written in the
  script are shown; labels come from "in 1879", "By 2016", "late 19th century", "now".
- **Branding** (`branding.py`): the video title over the first 2.5 s, a small watermark at the top
  left, and a white pop-up with the channel name (default at 3.0 s). Nothing of this appears when
  `branding.channel_name` is empty, except the title card. The name lives in `settings.yaml`.
- **Music** (`music.py`): by default a soft pad synthesised from sine waves, so it carries nobody's
  licence. Tracks the user puts in `assets/music/` (git-ignored; their licences are the user's to
  check) are used with `music.mode: tracks`. The music sits 22 dB down and ducks under the voice
  (sidechain). A failed music step means no music, not a failed video.
- **Transitions:** 0.25 s cross-fades (`render.transition_s`, 0 = hard cuts). Each shot except the
  last is rendered that much longer, so the total length is unchanged.
- **One final encode** joins shots, three ASS layers (graphics, branding, captions) and the audio mix.

## Found while testing

- `alimiter` has auto-level on by default: it raised the mix to -14.4 LUFS with a true peak above
  0 dB. `level=0` keeps it a limiter (result -16.2 LUFS, -1.4 dBTP).
- A pure sine at `volume=0.05` is below the silence threshold: test signals need real level.

## Not verified

- How the synthesised pad sounds, and how smooth the zoom, fades and bar growth look in motion: only
  still frames and levels were inspected. The user judges.
