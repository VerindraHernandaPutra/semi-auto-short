# 0007 — Cost tracking, provider configuration, dry-run, topic specificity

Status: accepted (2026-10-28)

## Cost tracking and budget (SPEC 14)

- Providers report token usage through `usage_listener`; `services/costs.py` turns it into
  `cost_entries` rows. Prices come from `pricing` in `settings.yaml` (USD per million tokens),
  never from code. A model without a price is stored with `cost_usd = NULL` and
  `cost_kind = "unpriced"` and shown as such, instead of silently costing zero.
  (`CostEntry.cost_usd` lost its Python-side default of 0.0 for this; no migration needed.)
- A cost row is committed immediately, so money spent stays recorded when the surrounding stage
  rolls back. Callers must not hold other uncommitted business changes while an LLM call runs.
- `check_budget` runs before every paid batch and raises `BudgetExceeded` when the monthly
  (Rp100.000) or per-video (Rp5.000) limit is reached; topic-queue spending has no job and counts
  toward the month only. The warning level is 80% of the monthly budget. `shorts costs` reports
  spend per operation and the rejected-versus-used topic ratio.
- Known gap: if the SDK raises while parsing an invalid model output, the response object is lost
  and that call's tokens are not recorded. Calls that end in a refusal or truncation are recorded.
- Known gap: Tavily credits and Pixabay request counts are not recorded yet.

## Providers and `--dry-run`

- `providers` in `settings.yaml` selects the real service for each role; unknown names fail
  loudly. Runtime overrides (API settings, dry-run) take precedence.
- `--dry-run` on `topics` and `produce` switches every service to its fake and runs against a
  throwaway in-memory database, so fake topics never enter the real queue and nothing is billed.
- Fact verification and scripting refuse fake providers on purpose, so a dry run of `produce`
  stops at the fact gate. Pipeline code still writes job folders under `data/jobs/`.

## Topic specificity

- Generic trivia is the main weakness of generated topics. Four layers address it: the prompt
  demands a named subject and a narrow angle; `overused_topics` and `generic_subjects` in
  `topic_policy.yaml` drive deterministic filters; self-rated specificity and novelty must reach
  `min_specificity` and `min_novelty`; footage queries must name the subject, so the footage check
  tests that exact subject.
- Self-ratings are weak evidence (models rate their own ideas generously), which is why the
  deterministic lists exist. Overlap matching uses a small suffix stemmer; it is not semantic.
- Specific subjects mean fewer stock clips, so more topics will fail the footage check. That is
  intended: a topic is dropped rather than filmed with unrelated footage.
