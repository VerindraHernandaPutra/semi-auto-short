# 0005 — Structured outputs for Claude, `basic` depth for Tavily

Status: accepted (2026-10-28)

## Context

Live checks on 2026-10-28 showed that `AnthropicProvider` forced a tool with
`tool_choice={"type": "tool"}`. `claude-sonnet-5-5`, the configured heavy model, rejects that with
HTTP 400, so claim extraction and scripting could not run. Schema field descriptions were in
Indonesian, and the model mirrored that language, producing Indonesian topics although video
content must follow `content_language` (English). Tavily was called with `search_depth="advanced"`,
which a live check measured at 2 credits per search, against 1 credit for `basic`.

## Decision

- `AnthropicProvider` uses `client.messages.parse(output_format=<Pydantic model>)`, which
  validates the output. Refusals, `max_tokens` stops, and unparsable output raise `LLMProviderError`;
  nothing is accepted silently.
- `generate_structured` takes an optional `effort` (low, medium, high; default medium) passed as
  `output_config.effort`. Thinking tokens bill as output, so callers should pick effort per task.
- Pydantic `Field` descriptions are English; a test fails if Indonesian words appear in them.
- Tavily depth and result count come from `config/settings.yaml` (`search.depth`, default `basic`).
  Full page text is still requested with the search (`include_raw_content`); a live check showed
  16-48K characters per result for 1 credit, so no separate Extract call is needed.

## Consequences

- Not yet implemented: refusal fallback to another model, and recording token usage in
  `cost_entries` (the provider returns only the parsed object). Both belong to later Phase 2 steps.
- Calls are still non-streaming with `max_tokens=16000`.
