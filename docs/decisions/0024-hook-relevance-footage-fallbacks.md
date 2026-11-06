# 0024 — Hooks chosen for relevance, footage that does not lose a paid script

Status: accepted (2026-10-30)

## Hook relevance

The two-domain rule picked "Cassini performed 22 dives" as the hook of "Saturn's rings are
disappearing": true and confirmed, but beside the point. A cheap rating call (light model,
`HookRelevance`, effort low) scores every distinct verified claim 1-10 for how directly it answers
the topic. Hook candidates are tried in that order, then the extraction's pick, then how many other
sources seem to state the claim. The two-domain rule is unchanged; an unreadable rating keeps the
old order. The extraction prompt also asks for a hook that states the core of the topic.

## Two-topic live run (Rp3,564, cap Rp4,500)

| Topic | Outcome | Cause |
|---|---|---|
| Olympus Mons | script passed, video dropped | shot 5 found no footage |
| Sun's corona | dropped | not enough verified claims or hook evidence (content) |
| Horsehead Nebula (auto-refilled) | job failed, run stopped | light model hit `max_tokens` while corroborating |

## Decisions

- **Footage in three levels** (`pick_footage`): the shot's own description; then the topic's
  subject (the footage queries checked when the topic was queued, else the topic's words), used
  when nothing fits or the best match is weak (below `clip.good_probability` 0.5) and the subject
  clip fits clearly; then the best clip already chosen, shown from another stretch. A paid script
  is no longer lost to one unfilmable sentence. Question words ("why") are no longer queries.
- **More negatives and tags for fiction:** astronauts or people in a science fiction scene, an
  abstract visual effect or explosion graphic, a human base or colony on another planet; avoided
  tags astronaut(s), colony, colonization, habitat, settlement. Found by looking at the frames:
  astronauts beside domes on Mars and a supernova burst for "magma" had passed.
- **Unusable answers are content problems:** `max_tokens` and `refusal` raise `LLMOutputError`
  (topic dropped, run goes on); an unreadable corroboration answer skips that page only. Light
  checks run at effort low so they do not think until the token limit.
- `finish_job` reports the recorded reason of a dropped job; topic texts lose pronunciation hints;
  a quality check flags reused footage.

## Verified

- Olympus Mons, footage and render rerun from the saved script and voice (no API cost): all shots
  Mars; the summit-caldera sentence shows the caldera; all 11 quality checks good.

## Not verified on a live run

- The relevance ranking and the `max_tokens` handling.
