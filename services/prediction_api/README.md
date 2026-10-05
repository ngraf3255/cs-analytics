# Prediction API

FastAPI service that loads the repository's `model.pkl` artifact and exposes
`/health`, `/options`, and `/predict` endpoints. The model was serialized with
scikit-learn 1.9.1, so that version is pinned in `requirements.txt`.

Run it from this directory after installing the requirements:

```sh
python -m pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Interactive API docs are available at `http://localhost:8000/docs`.

## Steam match sync (feature-flagged)

Steam sign-in, match-history linking and sync are **disabled** (routes return
`503 steam_sync_disabled`, `GET /steam/status` returns `{"enabled": false}`)
unless both `DATABASE_URL` and `TOKEN_ENCRYPTION_KEYS` are set. When enabled,
these are also required: `SESSION_SECRET` (32+ chars), `PUBLIC_API_URL`,
`FRONTEND_URL`, `STEAM_WEB_API_KEY`.

Optional: `SESSION_COOKIE_SAMESITE` (lax), `SESSION_COOKIE_SECURE` (true),
`SESSION_COOKIE_DOMAIN`, `SYNC_MAX_MATCHES_PER_REQUEST` (1),
`SYNC_MIN_INTERVAL_SECONDS` (30), `DEMO_MAX_DOWNLOAD_BYTES`, `DEMO_MAX_DECOMPRESSED_BYTES`.

Generate an encryption key (comma-separate several, newest first, to rotate):

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Apply migrations: `DATABASE_URL=... python -m steamlink.migrate`.

Demo URL resolution needs a CS2 Game Coordinator integration
(`UnconfiguredDemoLocator`, see TODO in `steamlink/valve.py`); until then sync
stops with `demo_retrieval_not_configured` and does not advance the cursor.

### Manual demo upload (`POST /matches/upload`)

Interim path while Game Coordinator demo retrieval has no bot account: a
signed-in user uploads a CS2 `.dem` or `.dem.bz2` and gets the same match list
entry and per-round report as a synced match. Same feature flag, session,
`X-Requested-With: csa` header and Origin check as the other Steam routes.

- Body: the raw file bytes (`Content-Type: application/octet-stream`), streamed
  to a random temp dir that is always deleted; the raw demo is never stored.
- Format is sniffed from content, not the filename: bzip2 (`BZh`) is
  decompressed first, then the file must start with the CS2 magic `PBDEMS2\0`
  (CS:GO `HL2DEMO` demos are rejected with `not_a_cs2_demo`).
- Limits: request body and decompressed demo `DEMO_MAX_DECOMPRESSED_BYTES`
  (1 GiB); a `.bz2` archive also `DEMO_MAX_DOWNLOAD_BYTES` (300 MiB). Over the
  limit -> `413 demo_too_large`. Your proxy/host body limit applies too.
- Idempotent per user on the SHA-256 of the decompressed demo (`.dem` and
  `.dem.bz2` of the same demo are the same match); known demos are not re-parsed
  (`"created": false`).
- One parse per process at a time; a concurrent upload gets `429 upload_busy`.
- Other errors: `422 demo_parse_failed`, `422 demo_has_no_rounds`. Nothing is
  stored on failure. Uploads never touch the share-code cursor.

Tests: `pip install -r requirements-dev.txt && pytest`

Real-demo end-to-end test (demoparser2 -> features -> model -> SQLite -> report).
Skipped unless `CSA_TEST_DEMO` is set; demos are too big to commit:

```sh
curl -L -o /tmp/test_demo.dem \
  https://raw.githubusercontent.com/LaihoE/demoparser/main/src/parser/test_demo.dem
CSA_TEST_DEMO=/tmp/test_demo.dem pytest tests/test_real_demo.py
```
