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

Two feature levels (`steamlink/config.py`); a route whose feature is off
returns `503 steam_sync_disabled`:

- **Demo upload + reports** (`/me`, `/matches*`, `POST /matches/upload`,
  `POST /auth/guest`, logout, `DELETE /me`): `DATABASE_URL` + `SESSION_SECRET`
  (32+ chars). No Steam key. `POST /auth/guest` starts a guest session (a
  `users` row with `steam_id = "guest:<hex>"`, no personal "you" analytics)
  only when `GUEST_UPLOADS=true`; by default it is off (503
  `guest_uploads_disabled`) and uploads need a Steam sign-in.
- **Steam** (OpenID login, `/steam/match-access`, `/steam/sync`,
  `/steam/auto-sync`, automatic sync): additionally `TOKEN_ENCRYPTION_KEYS` +
  `STEAM_WEB_API_KEY`, and then `PUBLIC_API_URL` + `FRONTEND_URL` are required.
  Guests get `403 steam_sign_in_required` on these. `STEAM_WEB_API_KEY`
  without `TOKEN_ENCRYPTION_KEYS` refuses to start.

`GET /steam/status`: `{"enabled": <steam, kept for older web builds>, "steam",
"upload", "guest", "auto_sync"}`.

Optional: `SESSION_COOKIE_SAMESITE` (lax), `SESSION_COOKIE_SECURE` (true),
`SESSION_COOKIE_DOMAIN`, `SYNC_MAX_MATCHES_PER_REQUEST` (3), `SYNC_JOB_MAX_ATTEMPTS` (5),
`SYNC_IMPORT_START_MATCH` (true),
`AUTO_SYNC_INTERVAL_SECONDS` (1800; 0 turns automatic sync off), `AUTO_SYNC_TICK_SECONDS` (60),
`AUTO_SYNC_MAX_USERS_PER_TICK` (5), `AUTO_SYNC_MAX_BACKOFF_SECONDS` (21600),
`SYNC_MIN_INTERVAL_SECONDS` (30), `DEMO_MAX_DOWNLOAD_BYTES`, `DEMO_MAX_DECOMPRESSED_BYTES`,
`DEMO_PARSE_ISOLATION` (`subprocess`: each demo is parsed in a short-lived child
process so its memory goes back to the OS; `inprocess` to debug),
`DEMO_PARSE_TIMEOUT_SECONDS` (600), `DEMO_PARSE_THREADS` (2), `UPLOAD_QUEUE_MAX` (3),
`UPLOAD_JOB_DIR` (`<tmp>/csa-upload-jobs`; uploads wait there for their parse job,
sync jobs download there).

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

Live end-to-end check (one command, prints PASS/FAIL/SKIP per step: Web API
key -> history walk -> GC demo URL -> download -> parse + model -> stored
round report): `python -m steamlink.live_check` (see
[`docs/live-e2e-checklist.md`](../../docs/live-e2e-checklist.md) for the inputs).
Offline with fake Valve + GC: `python -m steamlink.live_check --fake --demo match.dem.bz2`.

### Linking match history (`PUT /steam/match-access`, `GET /me`)

Body `{auth_code, share_code, consent}`; Valve is asked once (`GetNextMatchSharingCode`)
before anything is stored. Codes are accepted the way users paste them: the
auth code in any case, with spaces or without dashes; the share code bare or
inside CS2's `steam://rungame/730/…/+csgo_download_match%20CSGO-…` link (also
for `POST /matches/upload?share_code=`). Share codes are range-checked, not
only pattern-matched. While linked, an empty `auth_code` keeps the stored code
and only replaces the share code (Leetify-style: the auth code is given once, a
fresh share code after a break of 30+ days). Each `422` names the box to fix:
`invalid_auth_code_format`, `auth_code_is_share_code`, `auth_code_required`,
`invalid_auth_code` (Valve) / `invalid_share_code_format`,
`share_code_is_auth_code`, `invalid_share_code` (Valve); plus `consent_required`,
`429 valve_rate_limited`, `502 valve_unavailable`.

`share_code` is optional: signing in never needs a match, and someone without a
recent Competitive/Premier/Wingman match has no share code yet. An empty
`share_code` saves the auth code alone (format-checked only; Valve can't be
asked without a known share code) and `GET /me` shows
`match_access.awaiting_share_code: true`; `POST /steam/sync` answers
`409 needs_share_code` and auto-sync is paused (`paused_reason:
needs_share_code`) until the user adds a share code after a match (an empty
`auth_code` then keeps the stored one). While a share code is stored, an empty
`share_code` keeps it (new auth code only, checked with Valve against it).
Linked with neither code: `422 share_code_required`. No migration: the
`cursor_share_code` column stays `NOT NULL` and `""` means "no share code yet".

`GET /me` `match_access.needs_relink` is `{reason, field}` when the last sync
failed in a way only new codes fix, until the codes are updated:
`invalid_known_code` -> `share_code` (cursor expired or no longer valid),
`invalid_auth_code` / `credentials_unreadable` -> `auth_code`. Transient errors
(rate limits, Valve down) never set it. The web shows a re-link banner, opens
the form on the right box and pauses Sync.

### Steam sync (`POST /steam/sync`)

Downloading and parsing a demo takes minutes on Render's free plan, so the
request only walks the match history and the work runs on the same background
job worker as uploads (`steamlink/sync.py`, `steamlink/jobs.py`, migration
`0004`):

- The request (per-user lock, `SYNC_MIN_INTERVAL_SECONDS` between syncs) asks
  Valve for up to `SYNC_MAX_MATCHES_PER_REQUEST` (3) new share codes after the
  cursor. A match that is already stored and imported (upload with its share
  code, earlier sync) is skipped without a download. Every other one becomes a
  `steam_sync` job, and the cursor moves past it **in the same transaction**,
  so a match is never queued twice (one job row per user and share code).
- The match of the share code the user linked with is imported too
  (`SYNC_IMPORT_START_MATCH`, default on): users paste their latest match
  token and expect that match. Each sync queues it once if it isn't in the
  user's list and has no sync job yet (no Valve call; without a demo bot it is
  simply tried again on a later sync).
- Response: `202` if any job is queued (else `200`) with `status`
  (`up_to_date | partial | queue_full | error`), `queued`, `skipped`,
  `has_more`, `error` and `jobs` (same shape as upload jobs, plus `kind` and
  `share_code`). `GET /steam/sync` returns the sync status, `active_jobs` and
  the 10 most recent sync jobs (the UI polls it and resumes after a reload);
  `GET /matches/upload/{job_id}` works for sync jobs too.
- Job stages: `locating` (Game Coordinator) -> `downloading` (progress 0..1)
  -> `decompressing` (progress) -> `hashing` -> `parsing` (shared parse slot)
  -> `storing`. If the demo turns out to be stored already (same SHA-256, or
  uploaded while the job waited) the job is `done` with `created: false` and
  the stored match learns the share code.
- Queue: sync jobs count towards `UPLOAD_QUEUE_MAX` (3, all users and kinds).
  When it is full, sync stops before queueing (cursor not advanced) and answers
  `queue_full`; sync again later.
- Transient failures leave the job `failed` without a match: `demo_not_ready`
  (Valve hasn't published the demo yet, or the download failed),
  `demo_bot_auth_failed`, `demo_retrieval_not_configured`, `internal_error`.
  The next sync queues such jobs again, up to `SYNC_JOB_MAX_ATTEMPTS` (5) runs.
  Permanent failures store a stub match (`unavailable`: `demo_unavailable` /
  `demo_too_large`; `parse_failed`: `parser_error` / `demo_has_no_rounds`) and
  finish the job; a manual upload of that demo later fills the stub in. A demo
  still not ready on the last attempt becomes an `unavailable` stub.
- Restarts: sync jobs don't need a surviving file. An interrupted one is
  queued again and re-downloads (at most `SYNC_JOB_MAX_ATTEMPTS` runs, then a
  `parse_failed` stub); queued ones simply wait for the worker.

### Automatic background sync (`steamlink/autosync.py`, migration `0009`)

Leetify-style: linked users get new matches without pressing Sync. A scheduler
thread starts with the job worker (app lifespan) and stops on shutdown.

- Every `AUTO_SYNC_TICK_SECONDS` (60, ±10% jitter) it picks up to
  `AUTO_SYNC_MAX_USERS_PER_TICK` (5) users, longest-waiting first: match history
  linked, automatic sync not turned off, no sync running, not waiting for a
  re-link (`needs_relink`), and due: the last sync (manual or automatic) is
  older than `AUTO_SYNC_INTERVAL_SECONDS` (1800), or the scheduled
  `next_auto_sync_at` passed. Each is synced with **the same code as the Sync
  button** (`SyncService.sync`: per-user lock, cursor, background jobs).
- Several API processes / instances: only the holder of the `auto_sync` lease
  (`scheduler_leases` row, renewed each tick, taken over when it expires) runs a
  tick, so the per-tick cap is global; each user is also claimed with a
  compare-and-set on `next_auto_sync_at`, so no user is synced twice. Works the
  same on SQLite (single process) and PostgreSQL.
- Scheduling: up to date -> next in one interval (+ up to 10% jitter); more
  history (`partial`) or a full job queue -> again in about a minute; Valve
  `429` (`rate_limited`) -> the tick stops for everyone (the limit is per API
  key) and that user backs off; other errors / crashes -> exponential backoff
  (interval x 2^failures, at most `AUTO_SYNC_MAX_BACKOFF_SECONDS`, 6 h); a
  re-link error pauses the user until they enter new codes. A manual sync
  reschedules the next automatic one from its own finish.
- Auto sync leaves one job-queue slot free (it queues while fewer than
  `UPLOAD_QUEUE_MAX - 1` jobs are active) so uploads aren't refused because of
  it, and does nothing while demo retrieval isn't configured (no demo bot).
- Opt-out: `PUT /steam/auto-sync {"enabled": false|true}` (CSRF header).
  `GET /steam/sync` and `/me` (`sync`) include `last_synced_at` (last sync that
  finished OK) and `auto_sync`: `enabled` (the user's toggle), `active`,
  `paused_reason` (`turned_off | not_linked | needs_relink | server_disabled |
  demo_retrieval_not_configured`), `interval_seconds`, `next_at`,
  `last_run_at`, `last_error` (last automatic run), `failures`.
  `GET /steam/status` adds `auto_sync` {`enabled`, `available`,
  `interval_seconds`}.
- Render free plan: the instance sleeps after ~15 minutes without traffic, so
  automatic sync only runs while it is awake (it catches up when it wakes).

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
- Deduped across users (see below); a known, imported demo is not re-parsed. A
  plain `.dem` is hashed while it arrives, so a re-upload (or a known
  `?share_code=` already in the user's list) is answered at once with `200` and
  a finished job; `"created"` is true when the match was new to this user's
  list (e.g. another player had imported it), false when it already was there.
  A `.bz2` is recognised after decompressing in the job. Exception: a known
  match parsed by an older parser (`outdated`, below) is parsed again by the
  job and its rounds replaced; that job ends with `"updated": true`.
- One worker thread per process works through jobs (uploads and Steam sync
  downloads) oldest first, one parse at a time. At most `UPLOAD_QUEUE_MAX` (3)
  jobs, all users and kinds, may be queued or processing; more ->
  `429 upload_queue_full` (checked before the body is read).
  `GET /matches/upload` lists uploads only unless `?kind=steam_sync|all`.
- Restarts: on startup (and when a client polls a queued job with no worker
  running) the worker resumes interrupted jobs whose file still exists (at most
  two attempts, then `demo_parse_failed`), fails jobs whose file is gone with
  `server_restarted` (Render's disk is ephemeral: upload again), deletes
  finished jobs after 7 days and removes stray files. Assumes one API process
  per job directory (Render: one instance, one uvicorn worker).
- Job errors: `demo_parse_failed`, `demo_has_no_rounds`, `not_a_cs2_demo`,
  `demo_too_large`, `server_restarted`, `internal_error`. Nothing is stored on
  failure. Uploads never touch the share-code cursor.

### Match list and report (`GET /matches`, `GET /matches/{id}`)

Each match (list item, job `match`, report `match`) carries `map_name`,
`rounds_count`, `source` (how it arrived for this user), `imported_at` (when it
was added to this user's list), `date` / `date_source` / `played_at` (when the
match was **played** if known: Steam sync stores the Game Coordinator's match
time, `date_source: "played"`; CS2 demos carry no date, so uploads fall back to
`imported_at`, `date_source: "imported"`), `players_recorded`, `outdated` and `score`
(`{"ct": 13, "t": 5}`: rounds won by the team on each side **at the end**, read
from the demo's team round totals at the last kill plus the winners of later
rounds, see `demo_parser.final_score`; `null` for stubs and matches parsed
before migration `0006_match_score`). Checked against the team entities on the
last tick for the four public test demos (8-2, 6-2 surrender, 13-11 with a
knife round, 13-5).

**Warmup / knife rounds** (rounds that ended at or before the last
`begin_new_match`, i.e. before the server restarted into the real match) are
left out of everything: `rounds_count`, the report's rounds (numbered 1..N from
the match start), the final score, per-player rounds and every summary number
(`demo_parser.match_rounds`). The FACEIT test demo has a knife round: 24 rounds,
not 25, and `rounds_count` equals the final score's sum on all four test demos.

**No match date in a demo.** Checked on the four test demos (Valve MM, FACEIT,
HLTV/ESL, the demoparser2 SourceTV fixture): the header has map, server name,
build (`patch_version`) and a format GUID, the server cvars and events have no
time (`steamworks_sessionid_server` is an opaque id). So uploads stay dated by
when they were added, labelled "Added"; only Steam sync knows when a match was
played (the Game Coordinator's match time).

**`outdated`** is `null`, or `{"reason", "fix": "reupload"}` when the match was
parsed by an older parser (`matches.parse_version`, migration
`0008_parse_version`; current = `PARSE_VERSION` in `storage/base.py`):
`players_not_recorded` (before migration `0007`: no per-player stats) or
`parser_updated` (version 1: warmup / knife rounds may still be counted).
Demos aren't kept on the server (job files are deleted after parsing, Render's
disk is ephemeral, and replay URLs aren't stored), so there is no server-side
re-parse: uploading the same demo again re-parses it and replaces its rounds,
player rounds, `rounds_count` and score for every owner (a parse by an older
parser never overwrites a newer one). The migration only flags rows; it
rewrites nothing. `GET /matches/summary` counts them in
`totals.outdated_matches` (included as stored).

### Across previous matches (`GET /matches/summary`)

Analytics over every match in the signed-in user's list (shared matches count
for each owner; `match_owners`), computed on request (`steamlink/analytics.py`,
two queries, one model call per map): `totals` (matches, imported / stubs,
rounds, scored / unscored), `prediction` (the model's `hit_rate`,
`brier_score` on P(CT) vs the actual winner, `coin_flip_brier_score` 0.25 in
`model`, `opening_kill_baseline_hit_rate` = always back the side with the
opening kill, `calibration` bins of the favourite's probability 50-60 ... 90-100%),
`sides` (rounds won by the CT / T side), `opening_kills` (conversion overall,
by side, top 5 weapons, average time), `maps` (per map: matches, rounds, CT/T
wins, model hit rate / Brier), `unscored_reasons`, and `recent_form`
(`?recent=N`, default 10, 1-50: the last N imported matches newest first, their
totals vs the earlier ones, `hit_rate_change`). Rates are 0..1, `null` when
there is nothing to divide by; zero matches return zeros / nulls / empty
lists. Those all-player numbers count map sides of everyone in the match
(`scope` labels them `all_players`). The route is registered before `/matches/{id}`.

**`you`: the signed-in player's own numbers.** At parse time every player's
side each round is stored (`player_rounds`, migration `0007_player_rounds`,
`demo_parser.extract_player_rounds`): their last `player_spawn` of the round,
overridden by their side in that round's kills (at halftime the teams switch
without a new spawn event, so a round whose kills contradict most spawns gets
its spawn sides swapped), plus kills / deaths (exit frags count for the round
that just ended, like the scoreboard; the server's post-match kills don't),
opening kill / death and survived. Rounds before the last `begin_new_match`
(warmup, a knife round before the restart) have no player rows. Rows are per
player, not per owner, so every owner of a shared match sees their own side,
including owners who attached it without a parse. Verified on the four public
demos: every side equals `team_num` at that round's freeze end (`parse_ticks`)
and K/D equals the scoreboard totals. `you` has `matches`, `rounds`, `won`,
`win_rate`, `sides.ct|t` (`rounds` with a known winner, `won`, `win_rate`),
`results` (won / lost / tied / unknown from the final score and the side the
player ended on), `kills`, `deaths`, `kd`, `kills_per_round`, `survival_rate`,
`opening_duels` (taken / won / lost, round win rate after an opening kill /
death), `maps` (same per map) and `recent_form` (last `recent` matches vs
earlier: `win_rate_change`, `kd_change`, per-match result / first side / K-D).
`matches_without_you` counts demos the player isn't in (e.g. uploaded pro
matches) and `matches_unknown` those parsed before `0007` (no backfill: demos
aren't kept; uploading the demo again fills it in, see `outdated`). A player
with no spawn and no kill in a round normally gets no row (e.g. they
disconnected), except in a round with no `player_spawn` at all (the recording
started after the spawns, HLTV test demo round 1): there the next round's
players count, on the same side unless most of them switched. Matches are ordered
by `played_at`, else `imported_at`. `GET /matches/{id}` adds `you`
(`status` `in_match` | `not_in_match` | `unknown`, first / last side, rounds
won, K/D, opening kills / deaths, `score: {you, them}`, `result`) and per round
`you` (`side`, `won`, `kills`, `deaths`, `opening_kill`, `opening_death`,
`survived`, `win_probability` = the model's probability for the player's side).

### Tableau export (`GET /matches/export/rounds.csv`, `GET /matches/export/matches.csv`)

Signed-in only (401 otherwise); the user's own list only (uploads, Steam sync
and shared matches they are attached to via `match_owners`), never other
users' matches. `steamlink/export.py` builds both tables; the response is
streamed UTF-8 CSV (`Content-Disposition: attachment`, `Cache-Control:
no-store`, `X-Export-Version: 1`). `rounds.csv` has one row per stored round
of every imported match; `matches.csv` one row per match (stubs too, see
`status`). Warmup / knife rounds are never stored, so never exported; matches
from an older parser are exported as stored with `needs_reupload` = 1. The
`you_*` columns come from the user's SteamID64 per-player rounds and are empty
when they are not in the match / round. Dates are ISO 8601 UTC to the second,
plus `match_day` (`YYYY-MM-DD`); flags are `1`/`0`; text starting with
`= + - @` gets a leading `'` (CSV formula injection). Columns, meanings and
how to connect Tableau: [`tableau/README.md`](../../tableau/README.md).
The same tables offline from the database (read-only, SQLite or PostgreSQL):

```bash
python -m steamlink.export --database-url "$DATABASE_URL" --steam-id 7656119... --out-dir ../../tableau/app
# or from the repo root: python python/export_app_tableau.py --database-url ... --steam-id ...
```

The web app's summary panel has an "Export for Tableau" control (Rounds CSV /
Matches CSV) that downloads them with the session cookie.

### One match, many sources and users (dedupe)

The same match can arrive by upload and by Steam sync, in either order, and
from every player who was in it. It is stored **once** (`matches`) and listed
for every user who has it (`match_owners`: user, how it arrived for them,
when). A new arrival is "the same match" if a key matches a stored row:

| Key | Known when | Trusted across users? | Index |
| --- | --- | --- | --- |
| demo SHA-256 | whenever we had the file (upload, or the demo sync downloaded) | yes (we hashed the bytes) | `UNIQUE (demo_sha256)` (NULLs don't collide) |
| share code | Steam sync (`share_code_verified = 1`), or upload `?share_code=` (a hint) | only if verified on both sides | `UNIQUE (share_code)`; uploads without one store `upload:<sha256>` |
| Valve match id | decoded from the share code | as the share code | partial `UNIQUE (valve_match_id) WHERE valve_match_id <> 'upload'` |

On a match, nothing is downloaded or parsed again and no second row is
written: the match is added to the user's list, gains keys it lacked (an
upload learns its share code from a later sync; a verified share code replaces
a contradicting upload hint, whose row falls back to `upload:<sha256>`), and if
it was not imported (e.g. Steam had no demo: `unavailable`) it is upgraded with
the uploaded rounds. Sync checks the share code / match id **before**
downloading: a match another player already synced is attached at once
(`POST /steam/sync` counts it in `attached`), as is one this user uploaded with
its share code (`skipped`); the cursor still advances. An upload's share-code
hint only links to matches already in the uploader's own list, so a mislabelled
upload can't attach or overwrite another user's match; Steam sync re-downloads
such a match once to check its hash. The demo hash assumes Valve serves the
same bytes the CS2 client saved (a re-encoded file only matches via a verified
share code). Deleting an account removes the user from shared matches; a match
is deleted only when nobody else has it (`matches.user_id`, "first importer",
is handed to another owner). Migration `0005_shared_matches` (Python, so the
backfill can merge transitively) merges existing per-user copies.

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
