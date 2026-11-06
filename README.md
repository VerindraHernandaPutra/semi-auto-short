# Shorts Engine

A local Windows application that produces fact-checked science YouTube Shorts (currently about
space and astronomy). The system picks a topic, researches it, verifies every fact against trusted
sources, writes the script, narrates it, chooses stock footage, adds subtitles and graphics,
renders a 9:16 video and checks its quality. The person running it only watches the result,
approves it and uploads it by hand.

> Status: work in progress. The full pipeline runs end to end, but the pass rate across many topics
> has not been measured yet. Nothing is ever uploaded automatically.

## How a video is made

1. **Topic queue**: an LLM proposes specific topics; policy, duplicate, overuse and footage
   availability filters decide which ones are queued.
2. **Research**: web search for each topic; only pages from whitelisted tier A/B domains are kept,
   cleaned of menus and capped in size.
3. **Verification**: claims are extracted with exact quotes; every claim must quote its source,
   match its numbers and units, and pass a separate support check. The opening (hook) fact must be
   confirmed by two independent domains and is chosen for its relevance to the topic.
4. **Script**: written only from verified claims, then checked by deterministic rules and a
   per-sentence model check; rejected drafts are rewritten with precise feedback.
5. **Voice**: the user's own recording, or a clone of the user's voice made locally.
6. **Timing**: forced alignment of the script's own words to the narration (Whisper is used to
   confirm the recording follows the script).
7. **Footage**: stock clips ranked by what their picture shows (CLIP), not by uploader tags, with
   fiction and off-topic clips rejected.
8. **Render**: 9:16 composition, per-word highlighted subtitles, animated data bars, title card,
   channel watermark, music bed with ducking, cross-fades and a quiet ending.
9. **Quality gate**: loudness, subtitle sync, sentences actually heard, black or frozen picture,
   brightness, footage match and duration are measured and reported in plain language.

The title, description, source list and footage credits are written automatically. Every source
URL comes from the search provider, never from a language model.

## Tech stack

| Area | Technology |
|---|---|
| Language and tooling | Python 3.12, [uv](https://docs.astral.sh/uv/), Ruff, pytest |
| CLI and API | Typer (`shorts` command), FastAPI (local API on 127.0.0.1) |
| Database | SQLite (WAL) with SQLAlchemy 2 and Alembic migrations |
| Validation | Pydantic v2 (every LLM output is validated against a schema) |
| Language models | Anthropic Claude API with structured outputs (a larger model for extraction and scripts, a smaller one for checks) |
| Web research | Tavily Search API |
| Stock footage | Pixabay API (Pexels supported but optional) |
| Footage ranking | OpenAI CLIP ViT-B/32 (ONNX) on ONNX Runtime |
| Speech recognition | faster-whisper (CTranslate2) on an NVIDIA GPU with CUDA, CPU fallback |
| Word timing | wav2vec2 (ONNX) forced alignment with a NumPy CTC Viterbi |
| Cloned voice (optional) | Chatterbox (Resemble AI) in a separate Python environment with PyTorch CUDA |
| Numbers read aloud | num2words |
| Video and audio | FFmpeg and FFprobe with libass (ASS subtitles, loudnorm, xfade, sidechain ducking), libx264 or NVENC |
| Font | Poppins Bold (SIL Open Font License) |
| Model downloads | Hugging Face Hub (cached locally on first use) |

## Requirements

- Windows 10 or 11 (developed on Windows 11)
- Python 3.12 and `uv`
- FFmpeg and FFprobe on `PATH`, built with libass
- An NVIDIA GPU is recommended (developed on an RTX 3050 6 GB); the CPU works but is slower
- API keys: Anthropic, Tavily, Pixabay

## Setup

```powershell
git clone <this repository>
cd shorts-engine
uv sync
Copy-Item .env.example .env   # then fill in the API keys
uv run alembic upgrade head
uv run shorts doctor          # checks Python, FFmpeg, GPU, configuration, keys and the voice setup
```

`.env` holds the secrets and is never committed. `data/` (database, jobs, voice sample) and
`assets/music/` are git-ignored.

### Optional: cloned voice

The voice clone needs PyTorch with CUDA (about 2.5 GB), so it lives in its own environment and is
called through `tools/voice_worker.py`:

```powershell
uv venv voice-env --python 3.12
uv pip install --python voice-env\Scripts\python.exe torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
uv pip install --python voice-env\Scripts\python.exe chatterbox-tts
```

Set `VOICE_PYTHON` in `.env` to that environment's `python.exe`, and put 20 to 30 seconds of your
own voice at `data/voice/reference.wav`. Videos made with the cloned voice must be marked as
altered or synthetic content when uploaded to YouTube.

## Usage

```powershell
uv run shorts make --count 1 --max-cost 4000   # queue to finished video, cloned voice, cost cap in rupiah
uv run shorts make --count 1 --no-voice        # stop after the script, to record it yourself
uv run shorts finish <job-id> <recording.wav>  # turn your recording into the video
uv run shorts finish <job-id> --clone          # same, with the cloned voice
uv run shorts produce --count 1 --dry-run      # offline run with fake providers, no cost
uv run shorts topics                           # fill the topic queue
uv run shorts costs                            # spending this month
uv run shorts list                             # jobs and their states
uv run shorts serve                            # local API on http://127.0.0.1:8000
```

A finished job leaves `render/final.mp4`, `render/description.txt` and `render/quality.json` in
its folder under `data/jobs/`.

## Configuration

Non-secret settings live in `config/`:

- `settings.yaml`: providers, model names and prices, budgets (monthly and per video), verification
  thresholds, speech rate, footage rules, render, subtitles, graphics, branding (channel name),
  music and quality limits
- `topic_policy.yaml`: categories, series, forbidden and overused topics
- `source_whitelist.yaml`: trusted source domains by tier
- `style_guide.md`: the narrator persona and script rules, read on every script

## Costs and safety

- Every paid call is recorded per job; runs stop at the monthly or per-video budget and at a
  per-run cap (`--max-cost`).
- Tests never call paid APIs; real-service tests are marked `live` and run only on request.
- No secret is written to logs, the database or error messages.

## Development

```powershell
uv run pytest            # offline test suite
uv run ruff check .
uv run ruff format .
```

The code lives in `src/shorts_engine/`: `pipeline/` (stages), `services/` (orchestration and
costs), `providers/` (LLM, search, footage, speech recognition, voice, each with a fake for tests),
`domain/` (schemas and the job state machine), `db/`, `api/` and `cli.py`. Design decisions are
recorded in `docs/decisions/`, and the full specification is in `SPEC.md`.
