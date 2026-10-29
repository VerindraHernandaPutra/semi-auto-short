# 0015 — Clean pages before reading them, and make every script part checkable

Status: accepted (2026-10-29)

## Context

Three live space runs (Mars, Jupiter, Venus) produced no script. Saved pages showed that 55% of the
text the model paid to read was site navigation (a NASA page was 4% prose), that search returned
only 3 results although 8 cost the same Tavily credit, and that hook candidates were hedged or
specific to one study. A micro-replay on the saved Jupiter pages then reached the script stage and
exposed four defects of our own checks.

## Decision

- `clean_page_text` drops images, links, bare URLs and lines under 8 words before text is measured,
  cut, stored and quoted. Pages that are only navigation are rejected as too short.
- `search.max_results` is 8. Credits per search are unchanged.
- Extraction asks for one plain hook fact, returned once per source that states it. Hook candidates
  that other sources seem to state (`claim_overlap`) are tried first. The two-domain rule is unchanged.
- The title is checked without its series label and episode number (a label is not a fact).
- The per-statement model check receives spoken text. Pronunciation hints were judged as
  unsupported details.
- The series hook pattern is "The hook fact, stated plainly." Earlier patterns ("We often
  picture X", "Here is why") made the hook assert something no claim states.
- The script prompt says hook, title and ending follow the body rule, and gives a word target.

No verification threshold was loosened.

## Consequences

- Replay on saved Jupiter pages: hook confirmed by nasa.gov and yale.edu, script passed in 2
  attempts (115 words). Mars replay confirmed a ferrihydrite hook, not the queued iron-oxide angle.
- Measured on one topic only. The pass rate over many topics is unknown.
- A passing script is not a good script: it is a list of verified facts and needs a human read.
