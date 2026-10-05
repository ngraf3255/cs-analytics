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
`SYNC_MIN_INTERVAL_SECONDS` (30), `DEMO_MAX_DOWNLOAD_BYTES`, `DEMO_MAX_DECOMPRESSED_BYTES`,
`DEMO_PARSE_ISOLATION` (`subprocess`: each demo is parsed in a short-lived child
process so its memory goes back to the OS; `inprocess` to debug),
`DEMO_PARSE_TIMEOUT_SECONDS` (600), `DEMO_PARSE_THREADS` (2), `UPLOAD_QUEUE_MAX` (3),
`UPLOAD_JOB_DIR` (`<tmp>/csa-upload-jobs`; uploads wait there for their parse job).

Memory check on a real demo (Linux): `python scripts/measure_demo_memory.py
<demo.dem> --budget-mb 300 --max-retained-mb 30`, or `CSA_TEST_DEMO=<demo.dem>
pytest tests/test_demo_memory.py`.

Generate an encryption key (comma-separate several, newest first, to rotate):

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Apply migrations: `DATABASE_URL=... python -m steamlink.migrate` (on Render
this runs in the start command with `--if-configured`; see
[`docs/deploy-render.md`](../../docs/deploy-render.md) for the full deploy:
Blueprint, managed Postgres, env vars, custom domain, upload/memory limits).

Demo URL resolution goes through the CS2 Game Coordinator with a dedicated
demo bot (`STEAM_BOT_REFRESH_TOKEN`, see `docs/steam-demo-bot.md`); without it
sync stops with `demo_retrieval_not_configured` and does not advance the cursor.

### Manual demo upload (`POST /matches/upload`)

Interim path while Game Coordinator demo retrieval has no bot account: a
signed-in user uploads a CS2 `.dem` or `.dem.bz2` and gets the same match list
entry and per-round report as a synced match. Same feature flag, session,
`X-Requested-With: csa` header and Origin check as the other Steam routes.

- Body: the raw file bytes (`Content-Type: application/octet-stream`), streamed
  to `UPLOAD_JOB_DIR/<job id>.upload` (default `<tmp>/csa-upload-jobs`). The
  file is deleted as soon as its job finishes; the raw demo is never kept.
- **Parsing runs in the background** (`steamlink/jobs.py`): the response is
  `202 {"job": {...}}` as soon as the body is received. Poll
  `GET /matches/upload/{job_id}` (`{"job": ...}`; `404 upload_job_not_found`
  for unknown or other users' jobs); `GET /matches/upload?limit=10` lists the
  user's recent jobs, newest first, so a UI can resume after a reload. A job:
  `status` `queued | processing | done | failed`; `stage` while processing
  `decompressing | hashing | parsing | storing`; `progress` 0..1 while
  decompressing; `queue_position` (jobs ahead) while queued; `error` (a code
  below) when failed; `match` + `created` when done. Why: on Render's free plan
  (0.1 CPU) a full-length demo takes 15-50 s and a `.dem.bz2` several minutes.
- Format is sniffed from content, not the filename: a body that starts with
  neither bzip2 (`BZh`) nor the CS2 magic `PBDEMS2\0` is rejected at once
  (`422 not_a_cs2_demo`; CS:GO `HL2DEMO` demos too). An archive is decompressed
  in the job and must then start with `PBDEMS2\0` (else the job fails with
  `not_a_cs2_demo`).
- Optional query `?share_code=CSGO-...` (the match's sharing code) links the
  upload to the Valve match id (`422 invalid_share_code_format` if malformed).
- Limits: request body `min(UPLOAD_MAX_BYTES, DEMO_MAX_DECOMPRESSED_BYTES)`
  (1 GiB each by default); a `.bz2` archive also `DEMO_MAX_DOWNLOAD_BYTES`
  (300 MiB), and its decompressed size `DEMO_MAX_DECOMPRESSED_BYTES` (job error
  `demo_too_large`). Over the limit -> `413 demo_too_large`. Render has no body
  cap of its own; a Cloudflare-proxied hostname would cap at 100 MB.
- Deduped per user (see below); a known, imported demo is not re-parsed. A
  plain `.dem` is hashed while it arrives, so a re-upload (or a known
  `?share_code=`) is answered at once with `200` and a finished job
  (`"created": false`); a `.bz2` is recognised after decompressing in the job.
- One worker thread per process works through jobs oldest first and shares the
  parse slot with Steam sync (one parse at a time; a job waits for a running
  sync parse). At most `UPLOAD_QUEUE_MAX` (3) jobs, all users, may be queued or
  processing; more -> `429 upload_queue_full` (checked before the body is read).
- Restarts: on startup (and when a client polls a queued job with no worker
  running) the worker resumes interrupted jobs whose file still exists (at most
  two attempts, then `demo_parse_failed`), fails jobs whose file is gone with
  `server_restarted` (Render's disk is ephemeral: upload again), deletes
  finished jobs after 7 days and removes stray files. Assumes one API process
  per job directory (Render: one instance, one uvicorn worker).
- Job errors: `demo_parse_failed`, `demo_has_no_rounds`, `not_a_cs2_demo`,
  `demo_too_large`, `server_restarted`, `internal_error`. Nothing is stored on
  failure. Uploads never touch the share-code cursor.

### One match, many sources (dedupe)

The same match can arrive by upload and by Steam sync, in either order. It is
stored once per user: a new arrival is "the same match" if **any** key matches
a stored row:

| Key | Known when | Column / index |
| --- | --- | --- |
| share code | Steam sync, or upload with `?share_code=` | `UNIQUE (user_id, share_code)`; uploads without one store `upload:<sha256>` |
| Valve match id | decoded from the share code | partial `UNIQUE (user_id, valve_match_id) WHERE valve_match_id <> 'upload'` |
| demo SHA-256 | whenever we had the file (upload, or the demo sync downloaded) | `UNIQUE (user_id, demo_sha256)` (NULLs don't collide) |

On a match, no second row is written. The stored row gains keys it lacked
(an upload learns its share code from a later sync; never overwrites a known
key), keeps its `source` (first arrival), and if it was not imported (e.g.
Steam had no demo: `unavailable`) it is upgraded with the uploaded rounds. Sync
checks the share code / match id **before** downloading, so a match uploaded
with its share code is never downloaded again; the cursor still advances. The
demo hash covers uploads without a share code, assuming Valve serves the same
bytes the CS2 client saved (a re-encoded file would only match via share code).

Tests: `pip install -r requirements-dev.txt && pytest`

PostgreSQL (opt-in; a throwaway database you own, each test gets its own
schema that is dropped afterwards):

```sh
CSA_TEST_DATABASE_URL=postgresql://user:pw@127.0.0.1:5432/csa_test pytest
```

CI (`.github/workflows/api-tests.yml`) runs the suite on SQLite and on
PostgreSQL 17, both with the real-demo test.

Real-demo end-to-end test (demoparser2 -> features -> model -> SQLite/PostgreSQL -> report).
Skipped unless `CSA_TEST_DEMO` is set; demos are too big to commit:

```sh
curl -L -o /tmp/test_demo.dem \
  https://raw.githubusercontent.com/LaihoE/demoparser/main/src/parser/test_demo.dem
CSA_TEST_DEMO=/tmp/test_demo.dem pytest tests/test_real_demo.py
```
