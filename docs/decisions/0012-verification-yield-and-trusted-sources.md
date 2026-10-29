# 0012 — Verification yield fixes and a wider trusted-source list

Status: accepted (2026-10-28)

## Context

The first real `produce --count 1` run (3 topics, Rp929, 15 Tavily credits, 3 minutes) produced no
script. The checks worked as designed, but the yield was too low:

- A topic with four verified claims was dropped because the model's own pick for the hook was a
  claim with one source. Another verified claim might have had two, but was never tried. (In that
  case the two sources really disagreed, 12-23 m/s against 14-23 m/s, and the check rejected the
  claim correctly.)
- A valid claim ("more than 10 pigments") was rejected because the quote said "ten".
- Extraction returned long claims that bundled several facts; the support check marked many
  "partial" because the quote did not contain every detail.
- One topic found no tier A/B source at all. A survey of what Tavily returned showed reputable
  domains missing from the list: `pubmed.ncbi.nlm.nih.gov` (NIH), `tsapps.nist.gov` (NIST),
  `academic.oup.com` (Oxford University Press journals) and `pbs.org`.

## Decision

None of these loosens a verification rule.

- **Hook candidates.** The flagged claim is still tried first and is still rejected when it cannot
  stand on two independent domains. If that fails, other verified claims are tried in extraction
  order, with the same two-domain rule, quote check, number check and support check, up to
  `verification.max_hook_candidates` (4) in total. A claim that fails as an alternate stays an
  ordinary single-source claim, which SPEC 7.3 allows.
- **Numbers.** Spelled-out numbers are compared as digits ("ten" equals "10", "a dozen" equals
  "12"). Different numbers still differ.
- **Extraction prompt** (`build_extraction_prompt`): one simple fact per claim, every number, name
  and species present in the quote, no naming of a species the quote does not name, an exact
  single-passage quote, at most `verification.max_claims_extracted` (12) claims, and the topic as a
  hypothesis.
- **Trusted sources** (decided by the channel owner on 2026-10-28). Tier A gains `.gov`, `.ac.uk`,
  `science.org`, `pnas.org`, `academic.oup.com`, `royalsocietypublishing.org`, `plos.org` and
  `cell.com`; tier B gains `pbs.org`. Deliberately excluded: `researchgate.net` (papers uploaded by
  users, not reviewed), social networks, forums and video sites. Independence still counts a whole
  organisation once (`pubmed.ncbi.nlm.nih.gov` and `www.ncbi.nlm.nih.gov` are both `nih.gov`).

## Consequences

- More topics can reach a confirmed hook, at the cost of a few extra Haiku calls per topic.
- A `.gov` or `.edu` page is trusted by domain alone. The claim still has to pass the quote,
  number and support checks, so a non-scientific page cannot slip a fact through.
- Yield on real runs is still unmeasured after these changes.
