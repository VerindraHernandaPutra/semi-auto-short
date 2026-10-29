# 0014 — Number checks that accept paraphrases, and script feedback that can be acted on

Status: accepted (2026-10-28)

## Context

The third real run reached the script stage for two topics and lost both after three drafts each
("Segment contains a number or unit absent from its verified claims" and "Factual segment has no
verified claim references"). The drafts were not kept, so the cause could not be read. An offline
probe of the number check showed it rejecting legitimate wording:

- "23 meters per second" against a claim written "12-23 m/s": the unit "meters" differed from "m".
- "one of the fastest strikes": the pronoun "one" was read as the number 1.

`ScriptSegment.kind` also defaulted to `"factual"`, so a transition line whose `kind` was left out
silently became a factual segment without a claim. The rejection messages named neither the
segment nor the number, so three rewrites could not fix what they could not see.

These are demonstrated defects. They are not proven to be the cause of the failures of that run.

## Decision

- **Units are normalised** (`meter(s)`, `metre(s)`, `m` are one unit, likewise km, cm, mm, mi, in,
  ft, yd, s, min, h, day, week, month, year, g, kg, lb, ton, l, ml, Celsius, Fahrenheit).
  "Meters per second" and "m/s" both read as metres. Different units still differ: "kilometers"
  does not match "m".
- **A bare "one" is not a number.** "One meter" and "1" still are. The cost: "one heart" against a
  claim that says "three hearts" is no longer caught by the number check; the per-statement model
  check still flags it. This applies to claims and quotes as well as to scripts.
- **`kind` and `ending_kind` are required**, so the model must state them and the JSON schema
  lists them as required. A line can no longer become factual by omission.
- **Feedback names the problem.** `reference_problems` returns every problem of a draft, each
  naming the segment, quoting the sentence, listing the offending numbers (for example "40 m") and
  showing what the cited claims actually say, plus the claim ids that are allowed. The first
  problem is still available through `_validate_references` for callers that need one verdict.
- **Every draft is kept.** `script/attempts.json` in the job folder records each attempt's draft,
  its problems, the model checks and its outcome, for dropped topics too.

## Consequences

- Fewer valid scripts are rejected for wording alone, and a rejected draft can be diagnosed.
- Whether the script pass rate improves on real runs is unmeasured.
- Still open: the relevance of extracted claims to the queued topic (a "frozen puddle" topic
  produced verified claims about frost heave in soil).
