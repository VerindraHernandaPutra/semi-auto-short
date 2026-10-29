# 0013 — Claim-targeted corroboration search and an audit log

Status: accepted (2026-10-28)

## Context

The second real `produce --count 1` run (Rp1.599, 16 Tavily credits) found 2-3 tier A/B domains for
each of three topics and verified 7 of 10 extracted claims each time, but confirmed no hook claim.
The text of the saved sources showed why: the first research round is about the topic as a whole,
so its 2-3 domains approach it from different angles (a research paper and a news item, an
aquarium page and an agency page) and rarely state the same specific fact. Where a fact was shared
the check failed for a reason nobody could see, because the outcome of each corroboration attempt
was not stored.

## Decision

- **Claim-targeted search.** When the pages already collected cannot confirm a hook candidate on
  two independent domains, one extra search is made with that claim's text
  (`find_corroboration_pages`, one Tavily credit). The returned pages go through the same triage as
  research pages (tier from the URL, unique URLs, per-domain cap, bounded text, tier A/B only), and
  pages already used as sources are skipped. URLs still come only from the search provider.
- **Same rules.** A page found this way counts only if the quote is found in its text, the numbers
  and units match, and the separate support check says `supports`, and only if its organisation is
  not already supporting the claim. Nothing was loosened.
- **Bounded.** At most `verification.max_corroboration_searches` (4) searches per job, switchable
  with `verification.corroboration_search`. Before any paid call verification checks the budget and
  the monthly search quota for the worst case. A search that fails (authentication, quota, network)
  fails the job; it is never read as "nothing found". Search credits are recorded against the job.
- **Stored only if used.** A page becomes a `sources` row, with its text file, only when it supported
  a claim; the claim keeps every supporting source and quote.
- **Audit log.** Every attempt is recorded with a named outcome: `not_stated`, `empty_quote`,
  `quote_not_in_source`, `number_mismatch`, `support_partial`, `support_no` or `confirmed`, with
  the quote, its similarity score and the model's reason. `research/verification.json` holds, per
  hook candidate, the supporting domains, each attempt, the search made and the decision, and is
  written for dropped topics too.
- **Test isolation.** `tests/conftest.py` now points `DATA_DIR` at a temporary folder before the
  package is imported. Earlier tests had been writing job folders into the real `data/jobs/`.

## Consequences

- Cost per attempt rises by up to 4 Tavily credits and a few Haiku calls per hook candidate.
- Whether the yield improves is still unmeasured on real runs.
- The monthly search counter reads the database in use. Credits spent by runs against temporary
  databases are not in the real database's counter, so the limit of 900 leaves a margin below the
  free plan's 1000.
