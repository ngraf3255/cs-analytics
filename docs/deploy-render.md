# Deploying the API on Render (with Steam sync)

The prediction API (`render.yaml` → `csgooners-prediction-api`) runs as-is
with no extra configuration. Steam sign-in, linking and sync stay disabled
until the variables below are set.

## Database: Render managed PostgreSQL

The homelab has no reachable PostgreSQL, so use a Render managed Postgres for now.

1. Render dashboard → **New → PostgreSQL**. Pick the **same region** as the API service. Plan: free is fine for testing (check Render's current free-tier expiry/limits) or a paid plan for real data with backups.
2. Copy the **Internal Database URL** (`postgresql://…`) from the database page. The API rewrites `postgres://`/`postgresql://` to the psycopg 3 driver automatically.
3. On the API service, set `DATABASE_URL` to that internal URL.
4. Apply migrations once (Render shell on the API service, or locally with the External URL):
   ```sh
   cd services/prediction_api && python -m steamlink.migrate
   ```
   Re-run after deploys that add files under `services/prediction_api/migrations/`.
5. Later, moving to the homelab only requires a new `DATABASE_URL` and a `pg_dump | pg_restore`.

## Environment variables (API service)

| Variable | Required | Example / notes |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | yes (already set) | `https://csgooner.com,https://www.csgooner.com,http://localhost:5173` |
| `DATABASE_URL` | for Steam features | Render Postgres internal URL |
| `TOKEN_ENCRYPTION_KEYS` | for Steam features | Fernet key(s), newest first: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SESSION_SECRET` | when enabled | 32+ random chars: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `PUBLIC_API_URL` | when enabled | `https://api.csgooner.com` (exact; used for the OpenID return URL) |
| `FRONTEND_URL` | when enabled | `https://csgooner.com` |
| `STEAM_WEB_API_KEY` | when enabled | from https://steamcommunity.com/dev/apikey |
| `STEAM_BOT_REFRESH_TOKEN` | for demo download | see `docs/steam-demo-bot.md` |
| `SESSION_COOKIE_DOMAIN` | optional | `.csgooner.com` if the API is on a subdomain |
| `SESSION_COOKIE_SAMESITE` | optional | `lax` (default). Only use `none` if the API must stay on `onrender.com` (Safari will still block it) |
| `SYNC_MAX_MATCHES_PER_REQUEST` | optional | `1` (default) until memory/CPU is measured |

Steam features turn on when **both** `DATABASE_URL` and `TOKEN_ENCRYPTION_KEYS`
are set. The service then refuses to start if `SESSION_SECRET`, `PUBLIC_API_URL`,
`FRONTEND_URL` or `STEAM_WEB_API_KEY` is missing.

## API domain (needed for login cookies)

Browsers block third-party cookies, so the session cookie must be first-party
to `csgooner.com`. Add a custom domain `api.csgooner.com` to the Render service
(Settings → Custom Domains, then a CNAME at your DNS provider), and set
`PUBLIC_API_URL=https://api.csgooner.com`. Set the frontend build variable
`VITE_API_BASE_URL=https://api.csgooner.com` (GitHub Actions repo variable).

## Resource notes

- Demo download and parsing run inside the `POST /steam/sync` request. Free instances have ~512 MB RAM. Measure a real sync before raising batch size, and consider a paid instance or a background worker.
- The filesystem is ephemeral, which is fine: demos live only in a temp dir during parsing and are deleted in `finally`.
