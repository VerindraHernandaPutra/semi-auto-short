# 0023 — Space niche configuration, and what the third live run broke

Status: accepted (2026-10-30). The niche change deviates from SPEC.md (animals, ocean, everyday
phenomena); the user chose the space/astronomy flow and approved this configuration.

## Space configuration

- `topic_policy.yaml`: categories `planets_and_moons`, `stars_and_the_sun`, `space_phenomena`;
  series "Everyone Gets This Wrong", "Strange Worlds", "Cosmic Scale", all with the plain hook
  pattern (earlier patterns invited unverifiable claims, ADR 0015); forbidden: astrology, UFO and
  alien claims, unconfirmed discoveries; overused space topics; generic words planet/star/galaxy/
  universe. The style guide, the topic prompt and the schema examples now speak of space.
- Pre-flight before paying: `shorts doctor` checked `openai-whisper` (replaced in ADR 0016) and now
  checks the real dependencies and the cloned-voice environment; the mock search pages were too
  short for the page cleaning (ADR 0015), so `--dry-run` never got past research; a free
  Pixabay + CLIP check showed good footage for every candidate topic before the paid run.

## Third live run (Saturn's rings, Rp1,523 recorded; the run's cap was Rp4,000)

Research and verification worked (9 verified claims, 106 words, hook confirmed). The script stage
failed, and every cause was ours:

1. **Optional `claim_ids` were left out** by the model in drafts 1 and 3, although the sentences
   matched claims word for word. `claim_ids`, `hook_claim_ids` and `ending_claim_ids` are now
   required in the schema (as `kind` became in ADR 0014).
2. **The name check could never match a hyphenated name**: "D-ring" was compared with "d" and
   "ring". Hyphenated words in claims are now matched whole.
3. **Titles are Title Case**, so every title word looked like a name ("Are", "Slowly"). The name
   check skips the title; the model check still compares it with all claims.
4. **Draft 4 broke the schema and failed the whole job**, which stopped the run, and the error did
   not say which field. Off-schema output is now `LLMOutputError`, naming the field path and type
   (never the content). In the script stage it is a rejected draft; in research and verification
   it drops the topic (one unreadable claim check rejects only that claim). Broken keys or networks
   still fail the job and stop the run.
5. **That draft was billed but not recorded**: `messages.parse` raised before usage could be read.
   The provider now calls `messages.create` with the same schema (`anthropic.transform_schema`,
   checked identical for all 12 schemas), records usage, then validates.

Checked offline on the saved drafts: with these fixes draft 2 passes every free rule and would have
gone to the model check.

## Replay with the fixes (verified)

- The saved Saturn claims went through the script stage again: **the first draft passed** every
  free rule and the model checks (Rp504). `finish_job` with the cloned voice then made the video;
  all quality checks passed.
- Watching the result found one more defect the checks had missed: the sentence splitter ended a
  sentence at "Sept.", so the voice read "on Sept." and, after a pause, "fifteen, twenty
  seventeen" on its own, which Whisper did not hear. Abbreviations are now spelled out before
  synthesis ("September fifteenth, twenty seventeen"), and the forced-alignment words use the same
  reading.
- New quality check, **"kalimat terdengar"**: a sentence is flagged when more than 40% of its
  words, or its last two, were not heard by Whisper (written or spoken form). Calibrated on real
  data: it flags the broken Saturn ending and nothing in the Jupiter, Moon and recorded-voice
  videos. After the remake Whisper heard the full date and all ten checks passed.

## Still open

- Hook choice: the two-domain rule picked "Cassini performed 22 dives", a weak hook for "rings
  are disappearing"; relevance of the hook to the topic is not scored.
- One topic proves little about the pass rate; it needs more runs.
