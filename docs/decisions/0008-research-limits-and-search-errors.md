# 0008 — Research source limits, honest search errors, job-level costs

Status: accepted (2026-10-28)

## Context

An audit of `research.py`, `verify.py` and `script.py` against SPEC 7.2-7.4 found that the
stages after topic selection recorded no cost and had no budget check, took every page the search
returned (an estimated 45-90K input tokens for Sonnet), and that `TavilyProvider` printed and
swallowed every error, so a bad key or an exhausted plan turned into topics silently dropped as
`insufficient_evidence`.

## Decision

- **Costs and budgets per job.** `track_costs` attaches listeners to the LLM and search providers
  for the duration of a stage, so every billed call is stored with the job id. Research,
  verification and scripting call `check_budget` first, before any state change or paid call; a
  refused job stays in its previous state and can be resumed. `produce` returns an unstarted job's
  topic to the queue and keeps any job that already did paid work.
- **Search errors are errors.** `SearchProviderError` carries a `kind` (auth, quota, bad_request,
  network). Authentication, quota and bad requests stop at once and never retry; a timeout retries
  once. If every query fails the call raises instead of returning an empty list, so "nothing found"
  and "the call failed" cannot be confused. A failed search fails the job; it does not drop the
  topic. Messages never contain keys or URLs.
- **Search credits are tracked.** Each search stores its credits in `cost_entries.input_units`
  (`provider = "tavily"`). `search.monthly_credit_limit` (900 of the free plan's 1000) blocks a run
  that could overflow the plan, using the worst case of `max_queries` credits.
- **Bounded evidence.** At most `research.max_queries` (6) queries; unique URLs only; only tier A/B
  pages decided from the URL, never from the provider's own tier; at most
  `research.max_sources` (6) pages and `max_sources_per_domain` (2) per independent domain; text
  cut to `max_chars_per_source` (12000, about 3K tokens). Stored text is exactly what Sonnet reads
  and what quotes are checked against, so the 20,000-character cut in verification was removed.
- **Early drop.** Fewer than `min_distinct_domains` (2) independent domains can never satisfy the
  hook rule (SPEC 7.3 rule 5), so the topic is dropped before the extraction call is paid for.

## Consequences

- A topic with thin tier A/B coverage is dropped earlier and cheaper.
- Not done yet (next steps): corroboration of the hook claim across domains, the script prompt and
  style guide, duration from word count, rewrite attempts, and the `produce` loop.
- Tavily's `include_domains` could restrict searches to whitelisted domains; its behavior with the
  `.edu` wildcard is unverified, so it was not used.
