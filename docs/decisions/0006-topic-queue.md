# 0006 — Automatic topic queue

Status: accepted (2026-10-28)

## Context

SPEC 7.1 requires a self-filling topic queue with policy, duplicate and footage filters. The
previous `generate_topics` produced hardcoded Indonesian mock topics and queued everything as
`candidate` with no filtering.

## Decision

- `refill_topic_queue` generates candidates with the light model, filters them, scores them and
  stores them. Passing topics get status `queued`; filtered ones are stored as `rejected` with a
  machine-readable reason, so they also count for duplicate detection and for the dropped-vs-used
  ratio SPEC 14 asks for. No schema change was needed. `produce` and the API take the
  highest-scoring `queued` topic.
- Filter order, cheapest first: category and series must exist in `topic_policy.yaml`;
  duplicate check; policy check by the LLM against the forbidden list; footage check.
- **Policy check fails closed.** A missing verdict or a failed call never queues a topic.
- **Duplicates** are detected by word overlap (Jaccard on stemmed content words) against every
  stored topic, with the threshold in `settings.yaml`. This catches reworded titles but not
  paraphrases that share few words; embeddings stay a later improvement (SPEC 7.1).
- **Footage check** counts distinct clips of at least `min_clip_seconds` and 720p across the
  candidate's 2-3 queries and needs `min_footage_candidates`. It is deliberately a first pass:
  a live probe showed that nearly any common query returns many hits, and hit count says nothing
  about relevance. Relevance scoring with vision remains in Phase 3 (SPEC 7.8).
- The refill loop is bounded by `max_batches`; it asks for about twice the missing topics because
  some candidates are filtered out.
- The prompt is English, built from `style_guide.md`, `topic_policy.yaml` and `content_language`,
  and steers away from topics that need diagrams, anatomy or rare species (stock footage gaps).

## Consequences

- A refill uses one generation call and one policy call per batch, plus 2-3 footage searches per
  surviving candidate. Token usage is not yet recorded in `cost_entries`.
- Topics created before this change by the old mock code keep status `candidate` and are ignored
  by `produce`.
