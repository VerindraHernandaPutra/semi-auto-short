# 0009 — Hook claims are corroborated across independent domains

Status: accepted (2026-10-28)

## Context

SPEC 7.3 rule 5 requires the main hook claim to be supported by at least two sources from
different domains. The old code only counted a hook as confirmed when the extraction model happened
to restate the identical claim text from two sources; the prompt never asked for that, so almost
every topic was dropped with `hook_requires_two_independent_domains`. Nothing downstream enforced
the rule either: the script could use any verified claim as its hook.

## Decision

- Extraction marks exactly one claim as the hook. If the model states it from two independent
  domains already, nothing more is needed.
- Otherwise `find_corroboration` asks the light model, once per remaining independence domain,
  whether that source states the claim and for an exact quote. The model only proposes; the quote
  counts only if it passes the same rules as any claim: it is found in that source's stored text
  (similarity at or above `quote_similarity_threshold`), its numbers and units appear in it, and
  the shared support prompt (`build_support_prompt`) returns `supports`. Hallucinated, altered or
  partial quotes are rejected before they can count.
- The search stops at the first confirmed extra domain, tries each domain once (a second page of
  the same organisation never counts), and is limited to `verification.max_hook_candidates` (2)
  hook claims. An LLM failure fails the job; it is never treated as "not found".
- The hook claim keeps all supporting sources: `source_ids` lists them and `supporting_quote`
  holds one `[source id] quote` per source, so no schema change was needed.
- `Claim.is_hook` now means "confirmed by `verification.hook_min_domains` independent domains".
  A candidate that fails is stored as rejected with its reason; surplus hook flags stay ordinary
  claims. The script step refuses a hook that rests on any claim without that flag.

## Consequences

- Cost is small and bounded: at most one corroboration call and one support call per other domain
  per hook candidate, each reading one source of at most 12,000 characters.
- Independence is judged by domain, as in the spec. Two domains that both copy one press release
  still count as two; detecting shared origin is not attempted.
- The script prompt still needs rewriting (English, style guide, durations); the hook-eligible
  marker added to it is a stopgap for that step.
