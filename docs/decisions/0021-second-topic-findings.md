# 0021 — Findings from the second topic

Status: accepted (2026-10-30)

## Run

One live run with two queued topics, stopping at the first script (budget Rp5,000 per video):
Venus (dropped) then the Moon (script in 2 drafts). Cost Rp3,074. The Moon video was then made with
the cloned voice, CLIP footage choice and the quality gate (9 checks: 8 good, 1 flagged).

## Findings and changes

- **Venus was verified well** (7 claims, hook confirmed) but dropped at the script stage: draft 1 had
  8 segments, draft 2 named "Venus" in a sentence whose claim does not, draft 3 collapsed to 74
  words. Rewrites started from scratch and moved the fault elsewhere. Rewrites now receive the
  previous draft with its claim ids and are told to change only what the points require; the prompt
  also says to put sentences into one segment instead of adding segments, and to name the subject
  only where the cited claim does. **Not yet measured on a live run.**
- **Sources in the description were incomplete.** The hook claim exists once per confirming source
  (three here), the script cites one of them, and only that source was listed. Every source that
  states a cited claim is now credited (the cited claims' own sources first).
- **The series label appeared twice** ("...Earth: Everyone Gets This Wrong" over the video and
  again as a label). The working title is cleaned of the label; the label is added once, after
  " | ", only if the title stays within 60 characters.
- **Footage for abstract science is thin on Pixabay.** The gate flagged shot 1 (a stylised crescent
  moon, picture match 0.29). Shots about tidal friction and laser ranging got pleasant but generic
  clips (ocean, light rays, clouds).

## Open

- The channel name in `branding.channel_name` is a placeholder (`@NamaChannelAnda`); the planned web
  profile should set it.
