# 0003 — Footage providers: Pixabay primary, Pexels dormant

Status: accepted (2026-10-28)

## Context

SPEC 7.8 assumed Pexels. On 2026-10-28 Pexels' API access page showed
"New API key issuance is currently paused", with no end date, so a new key cannot be obtained.
Official docs were read the same day (Pixabay API, Pixabay license and terms, Pexels license and API,
NASA media guidelines, Coverr intro page).

## Decision

- Footage access stays behind `BaseFootageProvider`; every provider returns the same
  `FootageCandidate` model (ids, URLs, size, duration, contributor, attribution text).
- **Pixabay is the default provider.** Pexels stays implemented (current `/v1/videos/search`
  endpoint) and is selectable once a key exists. Provider files live in `providers/footage/`.
- Pixabay rules implemented from its documentation:
  - API responses are cached for 24 hours (file cache under `data/cache/pixabay/`;
    the API key is not part of the cache key or stored in cache files).
  - Rate limit is 100 requests per 60 seconds; HTTP 429 honors `Retry-After`, retries are bounded.
  - Videos have no orientation filter, so orientation is derived from width and height;
    landscape clips are cropped to 9:16 (allowed by SPEC 7.8).
  - Clips are downloaded to local storage, not hotlinked; the source is credited in `upload.md`.
  - `contributor_url` is left empty because the API does not document one; nothing is invented.
- Provider error messages never contain request URLs, because Pixabay takes the key in the
  query string.
- NASA's library is not used for animals (probe: `octopus` 0 videos, `jellyfish` 0). It may suit
  earth and space topics later; its terms include restrictions that need review first.

## Consequences

- Pixabay's license does not discuss YouTube Content ID. Asset ids are stored so a clip can be
  traced and dropped if a claim appears; Pixabay terms require stopping use of content under claim.
- Pixabay forbids bulk or systematic copying without permission; per-video downloads are fine.
- Real catalog coverage for the niche is unmeasured until a Pixabay key is used for a probe.
- `pipeline/footage.py` was adjusted only to read the new candidate model. It remains a
  Phase 3 placeholder (see the phase 1 report).
