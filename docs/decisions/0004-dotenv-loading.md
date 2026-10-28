# 0004 — Load `.env` at package import

Status: accepted (2026-10-28)

## Context

Providers read secrets with `os.getenv`, but nothing loaded `.env`, so keys stored there were
never seen by the CLI or API. Some values, such as `DATA_DIR`, are also read when
`shorts_engine.db.base` is imported, so loading has to happen before that.

## Decision

- `shorts_engine/__init__.py` calls `load_environment()` (in `shorts_engine/env.py`) using
  `python-dotenv` with `override=False`, so real environment variables always win.
- `SHORTS_ENV_FILE` selects another file; an empty value disables loading.
  `tests/conftest.py` sets it empty so a developer's real keys never reach the test run.
- `python-dotenv` is now a direct dependency. It was already installed through
  `fastapi[standard]`, so no new package was added.
- Values are never printed; `shorts doctor` reports only whether a key is present.

## Consequences

- `.env` stays untracked (already in `.gitignore`); `.env.example` lists every key name.
- Any entry point that imports `shorts_engine` gets the same behavior, including uvicorn workers.
