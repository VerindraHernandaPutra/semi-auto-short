# 0022 — Material gate, better rewrites, and one command from queue to video

Status: accepted (2026-10-30). Deviates from SPEC.md 7.4 on the number of rewrites; the user approved
raising it from two to three.

## Context

On the second topic (Venus) three drafts failed and cost Rp1,171, although the facts were verified.
A dry run of the new rewrite prompt on the saved Venus data showed the real cause: all seven verified
claims were already used and held 67 words, so no rewrite could reach the 91 words a script needs.

## Measured (distinct verified claims, in words, and the script they produced)

| Topic | Claim words | Script words |
|---|---|---|
| Venus | 67 | none (dropped) |
| Moon | 119 | 94 |
| Jupiter | 166 | 97 |

A script uses roughly 58-80% of its material, so a topic needs about 100 words of claims to reach 91.

## Decisions

- **Material gate** (`script.min_claim_words: 100`): a topic whose distinct verified claims hold fewer
  words is dropped before any draft is paid for (`insufficient_material:N_words_of_100`). Checked on
  the saved Venus data: dropped with no API call. The threshold comes from three samples; adjust it
  with data.
- **More material:** `verification.max_claims_extracted` 12 -> 18 (the Moon topic hit 12).
- **Rewrites that can succeed:** a rewrite sees the previous draft with its claim ids and is told to
  change only what the listed problems require; a draft that is too short is told how many words to
  add and which unused verified claims it can use; the prompt asks for the segment count and for the
  subject to be named only where the cited claim names it. `script.max_rewrites` 2 -> 3 (SPEC 7.4
  says twice; approved by the user). Verification rules are untouched.
- **One command:** `shorts make --count N` produces the scripts (dropped topics replaced), then makes
  each video with the cloned voice (`finish_job`: voice, alignment, footage, render, quality checks).
  A failing video does not stop the others. `--no-voice` stops after the scripts. `finish` uses the
  same `finish_job`.
- **Run cost cap:** `--max-cost` (rupiah) stops a run from starting new topics once its spending
  reaches the cap; a topic already under way stays bounded by the per-video budget.

## Not verified

- That the rewrite changes raise the pass rate on a live run, and that 100 words is the right
  threshold. Both need real runs to measure; one run proves little.
- Topic generation for the space channel: `config/topic_policy.yaml` and the style guide still
  describe animals and the ocean, so a refilled queue would not produce space topics yet.
