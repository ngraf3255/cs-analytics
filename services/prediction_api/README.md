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
`SESSION_COOKIE_DOMAIN`, `SYNC_MAX_MATCHES_PER_REQUEST` (3), `SYNC_JOB_MAX_ATTEMPTS` (5),
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
