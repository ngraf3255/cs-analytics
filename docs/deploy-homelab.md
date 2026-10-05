# Deploy baseline: homelab API + Postgres

**Chosen baseline (2026-10-05):** run FastAPI and Postgres together on a home
Proxmox VM. Site stays on Cloudflare; API is public at `api.csgooner.com`.

| Piece | Where |
| --- | --- |
| Frontend | Cloudflare Worker (`csgooner.com`) |
| API + Postgres | Homelab Proxmox VM: **2 vCPU** (R5 5600X host), **8 GB RAM** |
| Public API | `https://api.csgooner.com` → home (HTTPS on VM, or Cloudflare Tunnel) |

In-repo wiring: [`deploy/homelab/`](../deploy/homelab/) (`docker-compose.yml`,
`Dockerfile`, `Caddyfile`, `.env.example`, optional systemd unit).

## Why not Render Postgres / remote DB

- Render managed Postgres is paid; skipped.
- Do **not** open `5432` to the public internet.
- API and DB share the same VM, so Postgres stays on localhost / the compose
  network only.

## Capacity note

Overnight measurement on a tighter free-shape (0.1 CPU / 512 MB): a 441 MB
`.dem` finished in ~53 s. 2 cores + 8 GB is enough headroom for API + Postgres
and demo parse jobs.

## Assumptions (Homelab VM)

Proceed as if the VM already exists:

| Spec | Value |
| --- | --- |
| Role | API + Postgres colocated |
| vCPU / RAM | 2 / 8 GB |
| OS | Linux with Docker (preferred) or Python 3.11 + local Postgres 17 |
| Public `5432` | **closed** |
| LAN IP | **unknown until Homelab reports it** — use placeholder `REPLACE_WITH_VM_LAN_IP` |
| Public/WAN IP | **unknown** — use placeholder `REPLACE_WITH_HOME_PUBLIC_IP` |

Replace those placeholders in Cloudflare DNS / port-forward notes when known.
Nothing in the compose file requires the LAN IP at runtime; only your router /
Cloudflare config does.

## Quick start (Docker Compose)

On the VM, from a clone of this repo:

```sh
cd /opt/cs-analytics   # or wherever the repo lives
git pull origin main

cp deploy/homelab/.env.example deploy/homelab/.env
# Edit deploy/homelab/.env: POSTGRES_PASSWORD, TOKEN_ENCRYPTION_KEYS,
# SESSION_SECRET, STEAM_WEB_API_KEY, optional STEAM_BOT_REFRESH_TOKEN.

docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d --build
```

What that starts:

- **db** — Postgres 17; published only as `127.0.0.1:5432` (admin on the VM).
- **api** — migrate + uvicorn on `127.0.0.1:8000`.
- **caddy** — not started unless you add `--profile edge` (see HTTPS below).

Smoke on the VM:

```sh
curl -sS http://127.0.0.1:8000/health          # {"status":"ok"}
curl -sS http://127.0.0.1:8000/steam/status    # {"enabled":true,...} once secrets are set
```

### Migrate command

Compose runs migrations on every API start:

```sh
python -m steamlink.migrate --if-configured
```

Run by hand (from `services/prediction_api`, with `DATABASE_URL` set):

```sh
cd services/prediction_api
python -m steamlink.migrate
```

Same runner as Render: applies `services/prediction_api/migrations/*` in order
under a Postgres advisory lock.

## Environment variables

Copy [`deploy/homelab/.env.example`](../deploy/homelab/.env.example). Steam
features turn on when **both** `DATABASE_URL` and `TOKEN_ENCRYPTION_KEYS` are
set; the API then refuses to start if `SESSION_SECRET`, `PUBLIC_API_URL`,
`FRONTEND_URL`, or `STEAM_WEB_API_KEY` is missing.

| Variable | Example / notes |
| --- | --- |
| `POSTGRES_*` | Compose-only; builds `DATABASE_URL` for the api service |
| `DATABASE_URL` | `postgresql://csgooners:…@db:5432/csgooners` (compose) or `…@127.0.0.1:5432/…` (systemd) |
| `TOKEN_ENCRYPTION_KEYS` | Fernet key(s), newest first. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SESSION_SECRET` | ≥ 32 random chars |
| `STEAM_WEB_API_KEY` | https://steamcommunity.com/dev/apikey — domain `csgooner.com` |
| `PUBLIC_API_URL` | `https://api.csgooner.com` (OpenID realm + return URL) |
| `FRONTEND_URL` | `https://csgooner.com` (redirect after Steam login) |
| `ALLOWED_ORIGINS` | `https://csgooner.com,https://www.csgooner.com,http://localhost:5173,http://127.0.0.1:5173` |
| `STEAM_BOT_REFRESH_TOKEN` | optional; without it, sync stops at `demo_retrieval_not_configured` (uploads still work) |
| `UPLOAD_MAX_BYTES` | default 1 GiB; lower to `100000000` if you Cloudflare-proxy the API |
| `UPLOAD_JOB_DIR` | durable disk path (compose volume `/var/lib/csa/upload-jobs`) |
| `AUTO_SYNC_INTERVAL_SECONDS` | `1800` default; `0` disables background sync |

Full optional knobs: [`docs/deploy-render.md`](deploy-render.md) env table
(same settings; that doc is legacy Render-only).

### CORS / cookie notes

- `ALLOWED_ORIGINS` must list exact frontend origins (no `*`); credentials are
  on for the session cookie.
- Host-only cookie on `api.csgooner.com` is same-site with `csgooner.com`, so
  leave `SESSION_COOKIE_DOMAIN` unset and `SESSION_COOKIE_SAMESITE=lax`.

## HTTPS / reverse proxy

Pick **one** edge path. Postgres never goes through any of them.

### Option A — Caddy on the VM (`--profile edge`)

1. Port-forward WAN **80/443** → `REPLACE_WITH_VM_LAN_IP` (or bind the VM to a
   public IP).
2. Cloudflare DNS for `api.csgooner.com`: **A** → `REPLACE_WITH_HOME_PUBLIC_IP`,
   **DNS only (grey cloud)** recommended (see DNS section).
3. Start Caddy:

```sh
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env --profile edge up -d
```

[`deploy/homelab/Caddyfile`](../deploy/homelab/Caddyfile) terminates TLS for
`api.csgooner.com` and reverse-proxies to the `api` service (1 GiB body limit).

### Option B — nginx on the host

Terminate TLS on the host and proxy to `http://127.0.0.1:8000`. Sketch:

```nginx
server {
  listen 443 ssl http2;
  server_name api.csgooner.com;
  # ssl_certificate / ssl_certificate_key via certbot or your ACME client
  client_max_body_size 1024m;
  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
  }
}
```

### Option C — Cloudflare Tunnel (no open 80/443)

Run `cloudflared` on the VM targeting `http://127.0.0.1:8000`, and point
`api.csgooner.com` at the tunnel hostname in Cloudflare. Still prefer checking
upload size / timeout limits on the tunnel path; large `.dem` uploads may need
DNS-only + Option A/B instead.

## Cloudflare DNS for `api.csgooner.com`

| Record | Name | Content | Proxy |
| --- | --- | --- | --- |
| A | `api` | `REPLACE_WITH_HOME_PUBLIC_IP` | **DNS only** (grey) preferred |
| or CNAME | `api` | tunnel hostname | as required by Tunnel |

Why grey cloud: Cloudflare Free/Pro proxy caps uploads around **100 MB** and
has short response timeouts. Full-length demos exceed that. Keep the Worker for
`csgooner.com`; only the API hostname should avoid the orange cloud unless you
intentionally lower `UPLOAD_MAX_BYTES`.

Remove / replace the old CNAME to `*.onrender.com` when cutting over.

## Frontend production API URL

The Vite app and the GitHub Action default production base URL is
`https://api.csgooner.com`.

1. Confirm GitHub repo variable `VITE_API_BASE_URL=https://api.csgooner.com`
   (Settings → Secrets and variables → Actions → Variables). Safe to set
   explicitly even though it matches the new default.
2. Redeploy: Actions → **Deploy frontend to Cloudflare Worker** (or push a
   frontend change to `main` after this PR merges).

Local dev still uses the Vite `/api` proxy when `VITE_API_BASE_URL` is empty.

## Non-Docker (systemd)

1. Install Postgres 17 on the VM; listen on `127.0.0.1` only.
2. Create role/db matching `.env.example`.
3. Clone repo to `/opt/cs-analytics`, create venv, `pip install -r
   services/prediction_api/requirements.txt`.
4. Put secrets in `/etc/cs-analytics/api.env` (`DATABASE_URL=postgresql://…@127.0.0.1:5432/csgooners`,
   same Steam/CORS vars as above).
5. `sudo install -d -o csa -g csa /var/lib/csa/upload-jobs`
6. Install [`deploy/homelab/cs-analytics-api.service`](../deploy/homelab/cs-analytics-api.service)
   and enable it.
7. Put Caddy/nginx/Tunnel in front of `127.0.0.1:8000`.

## Cutover checklist

1. Secrets in `deploy/homelab/.env` (or `/etc/cs-analytics/api.env`).
2. Compose (or systemd) up; `/health` OK on loopback.
3. DNS: `api.csgooner.com` → home (`REPLACE_WITH_HOME_PUBLIC_IP` or tunnel).
4. HTTPS working: `curl https://api.csgooner.com/health`.
5. GitHub `VITE_API_BASE_URL` + frontend redeploy.
6. Browser: csgooner.com → Steam sign-in → upload or sync.
7. Player auth + share codes (see [`docs/live-e2e-checklist.md`](live-e2e-checklist.md)).

Live go-live steps beyond this page:
[`docs/live-e2e-checklist.md`](live-e2e-checklist.md). Legacy Render path (not
baseline): [`docs/deploy-render.md`](deploy-render.md).
