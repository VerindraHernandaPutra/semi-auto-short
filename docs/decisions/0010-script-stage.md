# 0010 — Script stage: style guide, word-count duration, rewrites

Status: accepted (2026-10-28)

## Context

The script prompt was Indonesian, ignored `style_guide.md`, `content_language` and the series
structures, and trusted the model's own duration estimate. Any failed check ended the job as
`FAILED`, although SPEC 7.4 asks for up to two rewrites and then dropping the topic. The
superlative and name checks of SPEC 7.4 existed only as a model judgement.

## Decision

- **Prompt** (`build_script_prompt`) is English and built from `config/style_guide.md` (read on
  every call), `content_language`, the series and their story structures, the topic, the verified
  claims (hook-eligible ones marked), and the numeric limits from `settings.yaml`.
- **Duration comes from the word count** at `speech.words_per_second`: 35-55 seconds is 91-143
  words at 2.6 words per second. Pronunciation hints in square brackets are not counted. The stored
  `estimated_duration_s` is computed, never the model's own figure.
- **Free rules run before the paid model check** (`script_rules.py`): claim references and hook
  eligibility, numbers and units, series name, 3-5 body segments, length, at most one "you",
  forbidden phrases, strong words (`script.overstatement_words`) allowed only when a cited claim
  states them, and capitalised names that the cited claims do not contain. Only a draft that passes
  them is sent to the per-statement model check, so a flawed draft costs one Sonnet call, not eight
  more Haiku calls.
- **Rewrites**: a rejected draft is regenerated with its problems listed as feedback, at most
  `script.max_rewrites` (2) times, walking `SCRIPTING -> SCRIPT_CHECK -> SCRIPTING`. After the last
  failure the topic is `DROPPED` (`script_check_failed: ...`), because the topic could not be
  scripted, whereas a provider error is `FAILED`. Each rewrite re-checks the budget first.
- **Series and episode**: the series must be one of the configured names; the episode number is
  counted from earlier jobs of that series, not invented by the model. Both are stored on the job.

## Known limits

- The name check only sees capitalised words inside a sentence; lower-case names and a name at the
  start of a sentence are left to the model check.
- `overstatement_words` is a fixed list, so a new kind of exaggeration passes the free rules.
- The hook pattern of "Everyone Gets This Wrong" used "Most of us picture X...", which the
  overstatement rule rejects. Decision of the channel owner (2026-10-28): the pattern now reads
  "We often picture X. That's not quite right." in `style_guide.md` and `topic_policy.yaml`.
  The rule itself is unchanged.
