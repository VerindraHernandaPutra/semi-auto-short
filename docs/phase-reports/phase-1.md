# Phase 1 Report — Backend Foundation

Date: 2026-10-28. Scope: SPEC.md section 17, Phase 1.

## 1. Completed (working and tested)

- Configuration loading and validation (`config/*.yaml`, `config/style_guide.md`) with
  actionable errors; `load_style_guide()` reads the file on each call.
- Rotating UTF-8 file logging (5 MB x 5 backups) under `data/logs/`.
- SQLAlchemy models and 3 Alembic revisions covering every table in SPEC section 9
  (`approvals`, `artifacts`, `assets`, `publications`, `segment_assets` included).
- Job state machine with `job_events` audit trail.
- CLI: `init`, `doctor`, `list`, `status`, `cancel`, `backup` (SQLite backup API).
- `doctor` checks Python, dependencies, FFmpeg/FFprobe, libass, `h264_nvenc`,
  GPU/CUDA/VRAM, config, data dir, migrations, API key presence.
  On the author's machine it reported RTX 3050 6 GB, NVENC and libass available.
- Provider interfaces with mock implementations for llm, search, footage and asr.

## 2. Files created or changed this session

- `migrations/env.py`, `src/shorts_engine/cli.py` — migration commit fix (ADR 0001).
- `config/style_guide.md` (moved from repo root), `src/shorts_engine/config.py` — loader.
- `pyproject.toml`, `uv.lock` — `ruff` dev dependency and lint config (ADR 0002).
- 28 files reformatted by `ruff format`; no behaviour change intended.
- `tests/unit/test_foundation.py` — style guide tests.

## 3. Architecture decisions

- [0001 Alembic explicit commit](../decisions/0001-alembic-sqlite-explicit-commit.md)
- [0002 Ruff policy](../decisions/0002-ruff-lint-policy.md)

## 4. Install and run (PowerShell)

```powershell
uv sync
uv run shorts init
uv run shorts doctor
uv run pytest
uv run ruff check .
```

## 5. Tests

`uv run pytest`: 47 passed, 0 failed. `ruff check` and `ruff format --check` pass.
Two tests failed at the start of the session; the cause was a real migration bug (ADR 0001).

## 6. Known problems / not done

- `pipeline/topics.py` falls back to hardcoded Indonesian mock topics. **Stub**, Phase 2.
- `pipeline/render.py`, `audio.py`, `footage.py` and `providers/video/pexels.py` are
  placeholders that predate their phase. They do not meet SPEC 7.6–7.9
  (`-c:v copy`, no timeouts, no relative `cwd`, `openai-whisper` instead of `faster-whisper`).
  Do not treat them as finished features.
- Pipeline code uses a hardcoded `Path("data")` instead of `get_data_dir()`.
- `services/` and `worker.py` do not exist yet; pipeline functions still take a DB session directly.
- Prompts are hardcoded and do not read `style_guide.md` yet (Phase 2).
- Fresh-machine install (`uv sync` on a clean checkout) has not been verified.
- Several earlier commits are large and mix topics.

## 7. User-provided items

None needed for Phase 1. Phase 2 needs `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `PEXELS_API_KEY`.

## 8. Cost estimates

No paid calls were made. Costs are not yet recorded (`cost_entries` has 0 rows).

## 9. Acceptance criteria (Phase 1)

| Criterion | Status |
|---|---|
| Job can be created and moved through valid states | Met (state machine tests) |
| `doctor` accurate on the user's machine | Output looks correct; user should confirm |
| Tests pass | Met (47/47) |
| Report written | This file |

## 10. Next steps

Phase 2 per SPEC 17: real topic generation and filters, claim verification rules (7.3),
script checks (7.4), cost/budget recording, `shorts produce --count N`.
