# Deploying the API on Render (managed PostgreSQL)

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
   Deploy logs should show `applied: 0001_steam_sync, 0002_cross_source_dedupe`
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
| `SYNC_MAX_MATCHES_PER_REQUEST` | `1` | raise only after measuring |
| `UPLOAD_MAX_BYTES` | `1073741824` | request-body cap for `POST /matches/upload` |
| `SESSION_COOKIE_DOMAIN` | not set | host-only cookie on `api.csgooner.com` is same-site with `csgooner.com`, so not needed |
| `SESSION_COOKIE_SAMESITE` | not set (`lax`) | only `none` if the API must stay on `onrender.com` |
| `DEMO_MAX_DOWNLOAD_BYTES` / `DEMO_MAX_DECOMPRESSED_BYTES` | defaults | 300 MiB `.bz2` / 1 GiB `.dem` |

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
- **Parsing runs inside the request** (kept simple on purpose). The body is
  streamed to a temp dir (deleted in `finally`), then parsed once per process
  (`429 upload_busy` for a concurrent upload). Time is fine: the 60 MB public
  CS2 demo parses in ~0.3 s on one core, so even at 0.1 CPU it's seconds, well
  under Render's limit. The browser shows "parsing" after the upload finishes.
- **Memory is the real risk on 512 MB plans.** Measured locally: the API idles
  at ~206 MB RSS (pandas + scikit-learn + model); parsing the 60 MB / 10-round
  demo peaks at ~319 MB. A full 24-30 round matchmaking demo (often 150-350 MB)
  has not been measured and may exceed 512 MB on `free`/`starter`. If uploads
  get OOM-killed (Render event "Out of memory"), move to a 2 GB plan (`1c-2g`),
  or lower `UPLOAD_MAX_BYTES`, or move parsing to a background worker.
- Free instances sleep after 15 minutes idle; the first request then takes
  about a minute. Disk is ephemeral, which is fine: demos only live in a temp
  dir during parsing.
