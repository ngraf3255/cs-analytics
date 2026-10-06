# Deploying the API on Render (managed PostgreSQL)

> **Not the chosen baseline.** Live deploy is the homelab VM (API + Postgres
> together): see [`docs/deploy-homelab.md`](deploy-homelab.md). This page stays
> as the old Render path / reference. `api.csgooner.com` below is the Render
> hostname only. The homelab API is **`api-site.csgooner.com`**.

`render.yaml` is a Render Blueprint for the prediction API
(`csgooners-prediction-api`) plus a managed PostgreSQL (`csgooners-db`). The
frontend is **not** on Render: it is a Cloudflare Worker at `csgooner.com`
(`.github/workflows/deploy-frontend.yml`).

Status: the Blueprint validates against Render's JSON schema, and the exact
`startCommand` was run locally against PostgreSQL 17 (migrations, then a real
CS2 demo upload -> parse -> model -> per-round report, then a restart with
"up to date" migrations). **Nothing has been deployed to Render yet.**

## What exists today (checked 2026-10-05)

- A live Render service at `https://cs-analytics-cwmo.onrender.com` (Oregon,
  running the old API without Steam routes: `/steam/status` is 404).
- DNS: `csgooner.com` uses Cloudflare nameservers, and `api.csgooner.com` is
  already a **DNS-only** CNAME to `cs-analytics-cwmo.onrender.com`. That is the
  right target, but `https://api.csgooner.com` returns **Cloudflare error 1000**
  because the domain is not attached to the Render service yet (step 4 below).

## Deploy steps

1. **Pick the service name.** The live service's URL suggests its name is
   `cs-analytics`; the Blueprint says `csgooners-prediction-api`. Before the first
   Blueprint sync, set `name:` in `render.yaml` to the existing service's exact
   name (Dashboard -> service -> Settings) so Render *adopts* it. Otherwise Render
   creates a second service with a new `onrender.com` URL and the CNAME must
   change.
2. **Merge to `main`** (or link the Blueprint to the branch you want to deploy).
   Render reads `render.yaml` from the linked branch.
3. **Dashboard -> New -> Blueprint**, pick `ngraf3255/cs-analytics`. Render
   creates `csgooners-db`, wires `DATABASE_URL` to its internal connection string,
   generates `SESSION_SECRET`, and asks for the `sync: false` values:

   | Prompted variable | Value |
   | --- | --- |
   | `TOKEN_ENCRYPTION_KEYS` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` (keep a copy in a password manager; losing it makes stored auth codes unreadable) |
   | `STEAM_WEB_API_KEY` | from https://steamcommunity.com/dev/apikey (domain `csgooner.com`). **Required** once the two variables above are set, or the service refuses to start |
   | `STEAM_BOT_REFRESH_TOKEN` | leave empty for now (see `docs/steam-demo-bot.md`); uploads work without it, Steam sync stops at `demo_retrieval_not_configured` |

   `sync: false` values are only prompted on **first** creation. For later syncs,
   add new secrets by hand in the service's Environment tab.
4. **Custom domain.** The Blueprint lists `api.csgooner.com`; confirm it on the
   service (Settings -> Custom Domains) and click Verify. The CNAME already exists.
   Keep it **DNS only (grey cloud)** in Cloudflare: proxying adds a 100 MB upload
   cap (Free/Pro plans) and a 100 s response timeout. If error 1000 persists after
   Render shows the domain as verified, re-check that the record is DNS only.
5. **Frontend.** Set the GitHub repo variable `VITE_API_BASE_URL=https://api.csgooner.com`
   (Settings -> Secrets and variables -> Actions -> Variables) and re-run
   "Deploy frontend to Cloudflare Worker". Today it falls back to the
   `onrender.com` URL, where the session cookie would be third-party.
6. **Smoke test** after the deploy is live:
   ```sh
   curl https://api.csgooner.com/health          # {"status":"ok"}
   curl https://api.csgooner.com/steam/status    # {"enabled":true,...}
   ```
   Then on csgooner.com: Sign in with Steam -> Upload a `.dem` -> open the match.
   Deploy logs should show `applied: 0001_steam_sync, 0002_cross_source_dedupe, 0003_upload_jobs, 0004_sync_jobs, 0005_shared_matches`
   once, then `applied: nothing (up to date)` on later deploys.

## Database

- Plan in the Blueprint: **free** (1 GB, no backups, **expires 30 days after
  creation**, deleted 14 days later). Fine for a trial; switch `plan:` to a paid
  plan (e.g. `basic-256mb`) before real users' data lives there.
- Region must equal the API's region (Oregon) so the internal URL works.
- `ipAllowList: []` = private network only. To use `psql`/`pg_dump` from a
  laptop, temporarily allow your IP in the database's Access Control and use the
  External URL.
- The API rewrites `postgres://` / `postgresql://` URLs to the psycopg 3 driver.
- Moving to the homelab later: new `DATABASE_URL` + `pg_dump | pg_restore`
  (only over a tunnel/VPN, with sign-off).

## Migrations

`python -m steamlink.migrate` applies `services/prediction_api/migrations/*.sql`
in order, each in a transaction, recorded in `schema_migrations`. On PostgreSQL
a session advisory lock serialises concurrent runners, so two instances
starting at once can't apply a migration twice.

They run in the **start command** (`--if-configured`: skipped when
`DATABASE_URL` is unset) because `preDeployCommand` needs a paid instance and
free instances have no shell. On a paid plan, move it to
`preDeployCommand: cd services/prediction_api && python -m steamlink.migrate`.

## Environment variables (API service)

| Variable | In Blueprint | Notes |
| --- | --- | --- |
| `PYTHON_VERSION` | `3.11.11` | |
| `ALLOWED_ORIGINS` | value | `https://csgooner.com,https://www.csgooner.com,http://localhost:5173,...` |
| `DATABASE_URL` | `fromDatabase` | internal URL of `csgooners-db` |
| `TOKEN_ENCRYPTION_KEYS` | `sync: false` | Fernet key(s), newest first (rotation) |
| `SESSION_SECRET` | `generateValue` | 32+ chars; Render generates 44 |
| `STEAM_WEB_API_KEY` | `sync: false` | required when Steam features are on |
| `PUBLIC_API_URL` | `https://api.csgooner.com` | exact; OpenID realm and return URL |
| `FRONTEND_URL` | `https://csgooner.com` | redirect after login |
| `STEAM_BOT_REFRESH_TOKEN` | `sync: false` | optional, demo bot |
| `SYNC_IMPORT_START_MATCH` | `true` (default, not in render.yaml) | also import the match of the share code the user linked with |
| `AUTO_SYNC_INTERVAL_SECONDS` | `1800` | automatic background sync of linked users (`0` = off); see the service README |
| `AUTO_SYNC_TICK_SECONDS` / `AUTO_SYNC_MAX_USERS_PER_TICK` / `AUTO_SYNC_MAX_BACKOFF_SECONDS` | defaults | 60 / 5 / 21600: scheduler check period, users synced per check (all instances), failure backoff cap |
| `SYNC_MAX_MATCHES_PER_REQUEST` | `3` | share codes walked per sync request; each new match becomes a background job (cheap request) |
| `SYNC_JOB_MAX_ATTEMPTS` | default | 5 runs of a sync job whose demo isn't ready / download failed, then an `unavailable` stub |
| `UPLOAD_MAX_BYTES` | `1073741824` | request-body cap for `POST /matches/upload` |
| `UPLOAD_QUEUE_MAX` / `UPLOAD_JOB_DIR` | defaults | 3 queued-or-running jobs (uploads + sync downloads, all users) / `<tmp>/csa-upload-jobs` (must be disk, not tmpfs) |
| `SESSION_COOKIE_DOMAIN` | not set | host-only cookie on `api.csgooner.com` is same-site with `csgooner.com`, so not needed |
| `SESSION_COOKIE_SAMESITE` | not set (`lax`) | only `none` if the API must stay on `onrender.com` |
| `DEMO_MAX_DOWNLOAD_BYTES` / `DEMO_MAX_DECOMPRESSED_BYTES` | defaults | 300 MiB `.bz2` / 1 GiB `.dem` |
| `DEMO_PARSE_ISOLATION` / `DEMO_PARSE_TIMEOUT_SECONDS` / `DEMO_PARSE_THREADS` | defaults | `subprocess` / 600 / 2 (see memory below) |

Steam features turn on when **both** `DATABASE_URL` and `TOKEN_ENCRYPTION_KEYS`
are set; the service then refuses to start if `SESSION_SECRET`, `PUBLIC_API_URL`,
`FRONTEND_URL` or `STEAM_WEB_API_KEY` is missing.

## Upload size, timeouts and memory

- **Request body:** Render documents no request-body cap (its comparison
  article lists payload limits as "unrestricted", request timeouts up to 100
  minutes: https://render.com/articles/deploy-nodejs-production-2026). So the effective
  upload cap is ours: `min(UPLOAD_MAX_BYTES, DEMO_MAX_DECOMPRESSED_BYTES)` = 1 GiB
  for a `.dem`, and a `.dem.bz2` is also capped at `DEMO_MAX_DOWNLOAD_BYTES`
  (300 MiB). If `api.csgooner.com` is ever Cloudflare-proxied, set
  `UPLOAD_MAX_BYTES=100000000`.
- **Parsing runs in the background** (`steamlink/jobs.py`, migration `0003`):
  `POST /matches/upload` streams the body to `UPLOAD_JOB_DIR`, records an
  `upload_jobs` row and answers `202 {"job": ...}` right away; the web UI polls
  `GET /matches/upload/{job_id}` every 2 s and shows queued / unpacking % /
  parsing / saving. One worker thread per process parses jobs oldest first in
  a short-lived child process (`python -m steamlink.parse_worker`), sharing one
  parse slot with Steam sync. At most `UPLOAD_QUEUE_MAX` (3) jobs may be queued
  or running (`429 upload_queue_full`). The job dir must be on disk, not a
  tmpfs, or the demo itself counts as RAM; Render's default is disk.
- **Steam sync uses the same worker** (migration `0004`): `POST /steam/sync`
  only asks Valve's match-history API for new share codes and queues one
  `steam_sync` job per new match (`202` + jobs, cursor advanced with the
  enqueue); the worker locates the demo (Game Coordinator), downloads it to
  `UPLOAD_JOB_DIR`, decompresses, parses and stores it. Sync jobs count
  towards `UPLOAD_QUEUE_MAX`; when the queue is full sync answers `queue_full`
  without moving the cursor. The UI polls `GET /steam/sync`.
  Measured 2026-10-05 on the free shape (512 MB + 0.1 CPU cgroup, Render
  start command, Valve faked by copying a local demo, so real download time
  comes on top): `POST /steam/sync` answered `202` in 0.2-0.3 s; a Valve-style
  72 MB `.dem.bz2` (112 MB matchmaking demo, 8 rounds) was imported 121 s
  later (12 CPU-s: ~105 s unpacking, ~13 s parsing), a 441 MB `.dem` in 53 s
  (4.8 CPU-s; this took 49 s *inside* the request before). `/health` stayed
  under 0.6 s, no OOM kill.
- **Restarts / deploys:** the disk is ephemeral, so a deploy or restart while a
  demo is queued or parsing loses the file; on startup that job is marked
  `failed` / `server_restarted` and the UI asks for the upload again (where the
  file survives, e.g. locally, the job is resumed; checked by SIGKILLing the API
  mid-parse and restarting it). Sync jobs survive restarts: an interrupted one
  is queued again and downloads its demo again (bounded by
  `SYNC_JOB_MAX_ATTEMPTS`). Free instances sleep after 15 minutes without
  requests; the UI keeps polling while a parse runs, so that only bites if the
  tab is closed during a parse that outlasts 15 minutes.
- **Memory on 512 MB plans (measured 2026-10-05, full-length demos):** the API
  idles at ~135 MB anonymous memory (~217 MB PSS with shared libraries). Peak
  anonymous memory (API + parse child) for a whole upload -> parse -> store ->
  score: ~228 MB on a 372 MB / 25-round FACEIT demo, ~232 MB on a 441 MB /
  18-round HLTV demo, ~231 MB for the same FACEIT demo as `.bz2`. demoparser2
  memory-maps the whole `.dem`, so total memory (PSS) reaches ~780 MB on the
  441 MB demo, but those are clean file pages the kernel drops under pressure:
  the exact Render start command (migrate + uvicorn) in a Linux cgroup with
  `memory.max=512M` and no swap took real HTTP uploads of all four test demos
  with no OOM kill, and the API's heap went back to ~150 MB after each. After
  each parse the child exits, so the API doesn't keep ~80 MB of parser heap.
  If memory runs out anyway, the child (oom_score_adj 1000) is killed, the
  upload fails with `demo_parse_failed` and the API keeps running (checked in a
  200 MB cgroup; the old in-process parse took the whole API down there).
  Re-measure with `python scripts/measure_demo_memory.py <demo> --budget-mb 300`.
- **CPU, not memory, is the slow part on `free` (0.1 CPU).** Measured
  2026-10-05 with the exact start command in a Linux cgroup with
  `memory.max=512M`, no swap and `cpu.max="10000 100000"` (0.1 CPU), real HTTP
  uploads (`csa-overnight/live_server_cpu.sh`; not on Render itself):

  | demo | before: one synchronous request | after: POST answered in | job done after |
  | --- | --- | --- | --- |
  | 58 MB SourceTV de_mirage | 14.8 s | 1.4 s | 14.7 s |
  | 108 MB Valve MM de_ancient | 16.5 s | 4.6 s | 19.3 s |
  | 372 MB FACEIT de_mirage | 41.7 s | 12.4 s | 47.5 s |
  | 441 MB HLTV de_nuke | 49.1 s | 14.3 s | 53.5 s |
  | 260 MB `.dem.bz2` (FACEIT, already imported as `.dem`) | 374.5 s | 5.7 s | 408 s |
  | same `.dem.bz2` into an empty database | n/a | 4.1 s | 427 s |

  CPU used: ~1.6 s (58 MB), ~4.7 s (441 MB), ~40 s for the `.bz2` (bzip2
  decompression is most of it). Cold start (migrate + uvicorn + model) is ~26 s
  wall / 2.6 CPU-s. `/health` stayed up throughout (worst 2.1 s, typically
  0.1 s; the 0.1 CPU quota itself adds up to ~100 ms per request), no OOM kill,
  and `DEMO_PARSE_TIMEOUT_SECONDS` (600) never fired: the slowest parse was
  ~40 s of wall time. The 6-7 minute `.bz2` is why parsing moved to a background
  job; uploading the plain `.dem` avoids the decompression entirely (the UI
  says so while unpacking). Render's CPUs may be slower than this box's, so
  treat these as lower bounds; a paid plan (0.5 CPU) should be ~5x faster.
- Parser settings: `DEMO_PARSE_ISOLATION` (`subprocess`, or `inprocess` to debug),
  `DEMO_PARSE_TIMEOUT_SECONDS` (600; a hung parse is killed and recorded as a
  parse failure), `DEMO_PARSE_THREADS` (2; demoparser2 threads in the child).
- Free instances sleep after 15 minutes idle; the first request then takes
  about a minute. Disk is ephemeral: uploaded demos only live in the job dir
  until their job finishes (see restarts above).
