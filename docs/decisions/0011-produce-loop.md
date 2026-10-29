# 0011 — The produce loop is a service, and dropped topics are replaced

Status: accepted (2026-10-28)

## Context

`produce` lived in the CLI, marked a topic `used` before any work, created one job per requested
script and counted a dropped topic as one of the requested scripts. `--count 3` could therefore
yield zero to three scripts, rejected topics were counted as used (which falsifies the
dropped-versus-used ratio of SPEC 14), the queue was never refilled, and `--dry-run` always found
an empty queue.

## Decision

- `services/produce.py::produce_scripts` owns the loop; the CLI and the API only call it and
  print the report (CLAUDE.md: pipeline logic does not live in the interface layers).
- It keeps producing until `count` scripts are ready. A dropped topic is marked `rejected` with
  `dropped:<reason>` and the next topic is taken, with no request to the user (SPEC 7.3). An empty
  queue is refilled automatically; a refill that finds nothing ends the run.
- Bounds: at most `produce.max_attempts_per_script` (3) topics per requested script; the run stops
  at the first `FAILED` job, since that usually means a broken key or service and continuing
  would only repeat it; and it stops at any budget or quota refusal.
- Topics follow their job: `used` while a job is active or finished, `rejected` when dropped, back
  to `queued` when a budget or quota check refused the job before any work, and `used` with
  `job_failed:<id>` when a job failed, so the topic stays tied to its resumable job.
- The report lists every attempt and the session cost (the change in this month's spend).
- `--dry-run` refills the throwaway in-memory queue with fake topics. Fake providers cannot verify
  facts, so a dry run ends in dropped topics; it exercises the plumbing, not the content.
- The Indonesian hook pattern conflict from ADR 0010 is guarded by a test: no series hook pattern
  may contain a word that the overstatement rule rejects.

## Consequences

- Cost per script is no longer predictable from a single attempt: dropped topics are paid for.
  The attempt cap and the budget checks are the safety nets, and `shorts costs` shows the ratio.
- The queue minimum of SPEC 7.1 is only topped up when it runs empty; `shorts topics --refill`
  keeps filling it to `topic_queue.min_queue_size` on request.
- A failed job is not retried automatically; there is no `retry` command yet.
