# 0001 — Explicit commit in Alembic online migrations (SQLite)

Status: accepted (2026-10-28)

## Context

`migrations/env.py` opened a SQLAlchemy 2 connection, ran `PRAGMA` statements, then ran
migrations without ever committing. Alembic logged every revision as applied, but the
connection rolled back on close. Real databases ended up half-migrated: some tables missing
and `alembic_version` stale or empty. `shorts doctor` and two tests exposed it.

## Decision

- Commit after the `PRAGMA` statements so migrations start in a clean transaction.
- Commit after `run_migrations()` so DDL and `alembic_version` persist.
- `_migrate_to_head()` creates the data directory itself, so it works without `shorts init`.

## Consequences

- Migrations are now durable and additive; existing rows are preserved
  (covered by `test_database_migration_is_additive`).
- Any database created with `shorts init` before this fix may be missing the latest
  revision. Run `shorts backup`, then `shorts init`; it is additive.
