# Deploy baseline: homelab API + Postgres

**Chosen baseline (2026-10-05):** run FastAPI and Postgres together on a home
Proxmox VM. Site stays on Cloudflare; API is public at `api.csgooner.com`.

| Piece | Where |
| --- | --- |
| Frontend | Cloudflare Worker (`csgooner.com`) |
| API + Postgres | Homelab Proxmox VM: **2 vCPU** (R5 5600X host), **8 GB RAM** |
| Public API | `https://api.csgooner.com` → home via **Cloudflare Tunnel** (default) or grey-cloud + Caddy/nginx |

In-repo wiring: [`deploy/homelab/`](../deploy/homelab/) (`docker-compose.yml`,
`Dockerfile`, `Caddyfile`, `backup-pg.sh`, `.env.example`, optional systemd units).

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
| Public/WAN IP | **usually dynamic** — prefer Tunnel; grey-cloud needs DDNS |

Replace those placeholders in Cloudflare DNS / port-forward notes when known.
Nothing in the compose file requires the LAN IP at runtime; only your router /
Cloudflare config does (and only for the non-Tunnel fallback).

### Network isolation (lab note)

This is the first internet-facing service in the lab. Put the VM on its own
VLAN or a Proxmox firewall group: inbound **80/443 only from WAN** (if not using
Tunnel), plus **SSH from LAN/VPN**; outbound internet only; **no access** to the
rest of `192.168.4.0/22` (DNS, VPN, hypervisor). Keep `5432` on loopback.

## Quick start (Docker Compose)

On the VM, from a clone of this repo:

```sh
cd /opt/cs-analytics   # or wherever the repo lives
git pull origin main

cp deploy/homelab/.env.example deploy/homelab/.env
# Edit deploy/homelab/.env: POSTGRES_PASSWORD, TOKEN_ENCRYPTION_KEYS,
# SESSION_SECRET, STEAM_WEB_API_KEY, optional STEAM_BOT_REFRESH_TOKEN,
# and CLOUDFLARE_TUNNEL_TOKEN if using the tunnel profile.

docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d --build
# Recommended edge (no open 80/443, survives WAN IP changes):
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env --profile tunnel up -d
```

What that starts:

- **db** — Postgres 17; published only as `127.0.0.1:5432` (admin on the VM).
- **api** — migrate + uvicorn on `127.0.0.1:8000` (trusts `X-Forwarded-*` from
  the compose network / local proxy).
- **db-backup** — `pg_dump` into volume `csa-pg-backups` on start, then every 24h.
- **cloudflared** — only with `--profile tunnel`.
- **caddy** — only with `--profile edge` (fallback; see HTTPS below).

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
| `CLOUDFLARE_TUNNEL_TOKEN` | required for `--profile tunnel` |
| `BACKUP_KEEP_DAYS` | default `14`; age prune for `csa-pg-backups` |
| `STEAM_BOT_REFRESH_TOKEN` | optional; without it, sync stops at `demo_retrieval_not_configured` (uploads still work) |
| `UPLOAD_MAX_BYTES` | **`100000000` (~100 MB) for Tunnel (Option A default)**; raise to `1073741824` only on Caddy/nginx grey-cloud fallback |
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

**Recommended default: Cloudflare Tunnel** (Option A). Residential WAN IPs
change; a static grey-cloud `A` record will silently break without DDNS. Tunnel
also avoids opening 80/443 on the house. Tradeoff: Tunnel uploads are capped at
**~100 MB** (see Option A); full demos need the Caddy/nginx fallback.

### Option A — Cloudflare Tunnel (recommended default)

1. In Cloudflare Zero Trust → Networks → Tunnels, create a tunnel for this VM.
2. Public hostname: `api.csgooner.com` → service `http://api:8000`
   (**compose** network name). If `cloudflared` runs on the **host** instead of
   compose, use `http://127.0.0.1:8000`.
3. Put the install token in `deploy/homelab/.env` as `CLOUDFLARE_TUNNEL_TOKEN`.
4. Start the tunnel profile:

```sh
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env --profile tunnel up -d
```

DNS for `api` becomes a CNAME/Tunnel route managed by Cloudflare (no home IP in
public DNS).

**Upload limit (hard):** Cloudflare's proxy (including Tunnel) caps request
bodies around **~100 MB**. Full-length CS2 demos are often larger and will get
**413** on this path. Keep `UPLOAD_MAX_BYTES=100000000` (the
[`deploy/homelab/.env.example`](../deploy/homelab/.env.example) default) so the
API rejects oversized bodies before Cloudflare does. For full demos, use
Option B/C (grey-cloud Caddy/nginx) and raise `UPLOAD_MAX_BYTES` to 1 GiB there.
Steam share-code sync still works over Tunnel when demos stay under the cap or
are fetched server-side by the bot.

### Option B — Caddy on the VM (`--profile edge`) — fallback

Use when Tunnel limits block large `.dem` uploads. Set
`UPLOAD_MAX_BYTES=1073741824` in `.env` for this path (Caddyfile already allows
1 GiB bodies).

1. Port-forward WAN **80/443** → `REPLACE_WITH_VM_LAN_IP` (or bind the VM to a
   public IP). **Only one host on the WAN IP can own 80/443.** If the lab already
   has a shared edge Caddy for other sites, skip this profile: add an
   `api.csgooner.com` site block on that proxy → `http://REPLACE_WITH_VM_LAN_IP:8000`,
   bind/publish the API on the VM LAN interface, and firewall that port to the
   proxy only.
2. Cloudflare DNS for `api.csgooner.com`: **A** → current home WAN IP,
   **DNS only (grey cloud)**. Grey cloud **exposes the home public IP** to anyone
   who resolves the name — acceptable for 1 GB uploads, but be aware. Because
   residential IPs are usually **dynamic**, run a DDNS updater (Cloudflare API
   token that upserts the `api` A record) or the API will go dark after a lease
   renew.
3. Start Caddy:

```sh
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env --profile edge up -d
```

[`deploy/homelab/Caddyfile`](../deploy/homelab/Caddyfile) terminates TLS for
`api.csgooner.com` and reverse-proxies to the `api` service (1 GiB body limit).
Uvicorn is started with `--proxy-headers --forwarded-allow-ips='*'` so Steam
OpenID return URLs and client IPs see `https` / the real client behind Caddy.

### Option C — nginx on the host — fallback

Terminate TLS on the host and proxy to `http://127.0.0.1:8000`. Same DDNS /
grey-cloud caveats as Option B. Sketch:

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

## Cloudflare DNS for `api.csgooner.com`

| Path | Record | Name | Content | Proxy |
| --- | --- | --- | --- | --- |
| **Default (Tunnel)** | CNAME / Tunnel route | `api` | tunnel hostname (Zero Trust) | as required by Tunnel |
| Fallback (Caddy/nginx) | A | `api` | current home WAN IP (+ **DDNS**) | **DNS only** (grey) |

Why grey cloud on the fallback: Cloudflare Free/Pro proxy (and Tunnel) caps
uploads around **100 MB**. Full-length demos exceed that, so the Tunnel default
keeps `UPLOAD_MAX_BYTES=100000000`; grey-cloud Caddy/nginx is the path for 1 GiB
uploads. Keep the Worker for `csgooner.com`; only the API hostname uses grey
cloud when you need large demo uploads.

Grey cloud also publishes the home WAN IP in public DNS. Prefer Tunnel when that
exposure or DDNS churn is undesirable.

Remove / replace the old CNAME to `*.onrender.com` when cutting over.

## Postgres backups

Compose service **db-backup** writes compressed plain SQL dumps to the
**`csa-pg-backups`** volume (separate from `csa-pgdata`):

- Path inside the container: `/backups/csgooners-YYYYMMDDTHHMMSSZ.sql.gz`
- Cadence: once on container start, then every 24 hours
- Retention: `BACKUP_KEEP_DAYS` (default 14)

Script: [`deploy/homelab/backup-pg.sh`](../deploy/homelab/backup-pg.sh).

Manual dump:

```sh
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env \
  exec -T db-backup /usr/local/bin/backup-pg.sh
```

List dumps (`compose run` needs a **service** name, not an image; the volume is
project-prefixed as `cs-analytics-homelab_csa-pg-backups`):

```sh
# Prefer exec against the running sidecar:
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env \
  exec db-backup ls -lah /backups

# Or one-shot against the named volume:
docker run --rm -v cs-analytics-homelab_csa-pg-backups:/backups busybox ls -lah /backups
```

### Off-VM copies (backups and data share one disk)

`csa-pg-backups` and `csa-pgdata` live on the **same VM disk**. A disk failure
takes both. Copy dumps somewhere else regularly:

```sh
# Example: rsync a dump to another host / NAS
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env \
  exec -T db-backup cat /backups/csgooners-YYYYMMDDTHHMMSSZ.sql.gz \
  > /tmp/csgooners-YYYYMMDDTHHMMSSZ.sql.gz
rsync -av /tmp/csgooners-*.sql.gz user@backup-host:/backups/cs-analytics/

# Or include the whole VM in Proxmox vzdump (schedule outside this compose stack)
# vzdump <VMID> --mode snapshot --compress zstd --storage <backup-storage>
```

### Calendar nightly (optional systemd timer)

If you want **03:15 local** instead of “every 24h from start”, scale the sidecar
down and install the host units:

```sh
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env \
  stop db-backup && docker compose -f deploy/homelab/docker-compose.yml \
  --env-file deploy/homelab/.env rm -f db-backup

sudo cp deploy/homelab/cs-analytics-pg-backup.service \
        deploy/homelab/cs-analytics-pg-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cs-analytics-pg-backup.timer
```

### Restore

```sh
# Pick a dump file from the backups volume, then:
gunzip -c csgooners-YYYYMMDDTHHMMSSZ.sql.gz \
  | docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env \
      exec -T db psql -U csgooners -d csgooners
```

Prefer restoring into a fresh empty database (or drop/recreate the schema) so
you do not mix old and new rows. Stop the **api** service while restoring.

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
7. Prefer Cloudflare Tunnel in front of `127.0.0.1:8000`; otherwise Caddy/nginx
   with DDNS. Schedule [`backup-pg.sh`](../deploy/homelab/backup-pg.sh) via the
   timer units or host cron against local `pg_dump`.

## Cutover checklist

1. Secrets in `deploy/homelab/.env` (or `/etc/cs-analytics/api.env`), including
   `CLOUDFLARE_TUNNEL_TOKEN` if using Tunnel.
2. Compose (or systemd) up; `/health` OK on loopback; confirm a dump appeared
   under `csa-pg-backups`.
3. DNS: Tunnel route for `api.csgooner.com` (default), **or** grey-cloud A + DDNS
   to `REPLACE_WITH_HOME_PUBLIC_IP`.
4. HTTPS working: `curl https://api.csgooner.com/health`.
5. GitHub `VITE_API_BASE_URL` + frontend redeploy.
6. Browser: csgooner.com → Steam sign-in → upload or sync.
7. Player auth + share codes (see [`docs/live-e2e-checklist.md`](live-e2e-checklist.md)).

Live go-live steps beyond this page:
[`docs/live-e2e-checklist.md`](live-e2e-checklist.md). Legacy Render path (not
baseline): [`docs/deploy-render.md`](deploy-render.md).
