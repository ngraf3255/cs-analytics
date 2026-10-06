# Deploy baseline: homelab API + Postgres

**Chosen baseline (2026-10-05):** run FastAPI and Postgres together on a home
Proxmox VM. Site stays on Cloudflare; API is public at **`api-site.csgooner.com`**.

| Piece | Where |
| --- | --- |
| Frontend | Cloudflare Worker (`csgooner.com`) |
| API + Postgres | Homelab Proxmox VM **`counterstrike`** (`192.168.4.54`, headless Debian 13): **4 vCPU** (R5 5600X host), **~6 GB RAM** (5.7 GiB usable) |
| Public API | `https://api-site.csgooner.com` → Cloudflare Tunnel → host `cloudflared` (systemd) → `http://localhost:8000` |

> **Hostname:** the public API is `api-site.csgooner.com`, **not**
> `api.csgooner.com`. The old `api` record was the legacy Render CNAME
> ([`deploy-render.md`](deploy-render.md)) and is not used by the homelab.

In-repo wiring: [`deploy/homelab/`](../deploy/homelab/) (`docker-compose.yml`,
`Dockerfile`, `Caddyfile`, `backup-pg.sh`, `.env.example`, optional systemd units).

## Why not Render Postgres / remote DB

- Render managed Postgres is paid; skipped.
- Do **not** open `5432` to the public internet.
- API and DB share the same VM, so Postgres stays on localhost / the compose
  network only.

## Capacity note

Overnight measurement on a tighter free-shape (0.1 CPU / 512 MB): a 441 MB
`.dem` finished in ~53 s. 4 vCPU + ~6 GB (5.7 GiB usable) is enough headroom for API + Postgres
and demo parse jobs.

## Current homelab setup

| Spec | Value |
| --- | --- |
| VM name | `counterstrike` (Proxmox) |
| LAN IP | `192.168.4.54` |
| OS | Debian 13, headless; Docker + Compose plugin |
| vCPU / RAM | 4 / ~6 GB (5.7 GiB usable) |
| Role | API + Postgres colocated (compose stack in this repo) |
| Public hostname | `api-site.csgooner.com` |
| Edge | Cloudflare Tunnel; **`cloudflared` runs on the VM host as a systemd service** (installed with the Cloudflare dashboard install script) |
| Tunnel public-hostname service | `http://localhost:8000` |
| API bind | compose publishes the API on **`127.0.0.1:8000`** only |
| Compose `cloudflared` sidecar | **not used** (don't start `--profile tunnel`) |
| Public `5432` | **closed** (loopback only) |
| Public/WAN IP | dynamic; doesn't matter because the Tunnel dials out |

`192.168.4.54` is only for SSH / LAN admin. Nothing public points at it, and no
router port-forward is needed on the Tunnel path. It only matters for the
grey-cloud fallbacks below.

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
chmod 600 deploy/homelab/.env
# Edit deploy/homelab/.env: POSTGRES_PASSWORD, TOKEN_ENCRYPTION_KEYS,
# SESSION_SECRET, STEAM_WEB_API_KEY (see "Steam Web API key" below),
# optional STEAM_BOT_REFRESH_TOKEN. No tunnel token needed: cloudflared
# already runs on the host.

# No --profile flags: host cloudflared is the edge.
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d --build
```

After the first bring-up, later deploys are automatic from `main`; see
[Auto-deploy from `main`](#auto-deploy-from-main).

What that starts:

- **db**: Postgres 17, published only as `127.0.0.1:5432` (admin on the VM).
- **api**: migrate + uvicorn, published as **`127.0.0.1:8000`**. Host
  `cloudflared` forwards `api-site.csgooner.com` here. It trusts `X-Forwarded-*`
  so Steam OpenID sees `https`.
- **db-backup**: `pg_dump` into volume `csa-pg-backups` on start, then every 24h.
- **cloudflared** (compose sidecar): **not used** on `counterstrike`. It is only
  started with `--profile tunnel`, so leave that flag off. Running it next to the
  host service would register a second connector for the same tunnel.
- **caddy**: only starts with `--profile edge` (grey-cloud fallback; see HTTPS below).

Smoke on the VM, then through the tunnel:

```sh
curl -sS http://127.0.0.1:8000/health          # {"status":"ok"}
curl -sS http://127.0.0.1:8000/steam/status    # {"enabled":true,...} once secrets are set
systemctl status cloudflared --no-pager        # host tunnel service: active (running)
curl -sS https://api-site.csgooner.com/health  # {"status":"ok"} from anywhere
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

## Steam Web API key

The API needs a Steam Web API key (`STEAM_WEB_API_KEY`) for Steam sign-in and
match sync. Create it once and store it **only** in `deploy/homelab/.env` on the VM.

**Before you start:** use a **non-limited** Steam account, meaning one with at
least **$5 USD spent** in the Steam store (a purchase or wallet top-up).
Limited accounts can't get keys. Use your main account, not the demo bot
account. Steam also asks you to confirm in the **Steam Mobile app** (Steam
Guard Mobile Authenticator), so have your phone ready. If the page says you
aren't eligible even though you've spent $5, make the profile public and try again.

1. Go to **https://steamcommunity.com/dev/apikey** and sign in with Steam.
2. **Domain Name:** enter `csgooner.com`.
3. Tick the box to agree to the Steam Web API Terms of Use, then click
   **Register**. If the Steam Mobile app shows a confirmation prompt, approve it.
4. Copy the key and put it in `deploy/homelab/.env` on `counterstrike`:

   ```sh
   cd /opt/cs-analytics            # repo root on the VM
   nano deploy/homelab/.env        # set: STEAM_WEB_API_KEY=<your key>
                                   # and set/uncomment TOKEN_ENCRYPTION_KEYS=<fernet key>
   chmod 600 deploy/homelab/.env
   docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d
   curl -sS http://127.0.0.1:8000/steam/status   # "enabled": true
   ```

   Set both in the same edit; see [Turning Steam on](#turning-steam-on) for why.

   The variable name is exactly **`STEAM_WEB_API_KEY`**. That's what
   `services/prediction_api/steamlink/config.py` reads, and it matches
   [`deploy/homelab/.env.example`](../deploy/homelab/.env.example).

**Never commit the key.** `deploy/homelab/.env` is git-ignored. Don't paste the
key into issues, PRs, chat, or `.env.example`. If it leaks, open the same page,
click **Revoke My Steam Web API Key**, register a new one, and update `.env`.

## Environment variables

Copy [`deploy/homelab/.env.example`](../deploy/homelab/.env.example). Two
feature levels (`services/prediction_api/steamlink/config.py`):

- **Demo upload + match reports**: `DATABASE_URL` + `SESSION_SECRET` (32+ chars).
  No Steam key needed. Visitors can upload a `.dem` as a guest (browser session,
  `POST /auth/guest`); set `GUEST_UPLOADS=false` to require a Steam sign-in.
- **Steam sign-in + sync**: additionally `TOKEN_ENCRYPTION_KEYS` +
  `STEAM_WEB_API_KEY`; then `PUBLIC_API_URL` and `FRONTEND_URL` are required.
  `STEAM_WEB_API_KEY` without `TOKEN_ENCRYPTION_KEYS` refuses to start.

`curl -sS http://127.0.0.1:8000/steam/status` shows what is on:
`{"enabled": <steam>, "steam": …, "upload": …, "guest": …}`. Startup logs a
line naming what is off and why.

### Turning Steam on

On `counterstrike` Steam is currently **off on purpose**: `STEAM_WEB_API_KEY`
is empty in `deploy/homelab/.env`. With `DATABASE_URL` + `SESSION_SECRET` set the
API runs in **upload-only** mode: guests upload `.dem` files and get match
reports, `/steam/status` reports `"steam": false, "upload": true`, and the site
shows Connect Steam as "coming soon". Setting `TOKEN_ENCRYPTION_KEYS` early is
harmless now (it no longer crash-loops without the Steam key).

To enable:

1. Set `STEAM_WEB_API_KEY=<key>` ([guide](#steam-web-api-key)).
2. Set `TOKEN_ENCRYPTION_KEYS` to a Fernet key (uncomment the prepared line in
   `.env`, or generate one with the command in the table below).
3. Restart:
   `docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d`
   (or `/opt/cs-analytics/deploy/deploy.sh`), then check
   `curl -sS http://127.0.0.1:8000/steam/status` shows `"enabled": true` (and
   `"steam": true`). Guest uploads keep working next to Steam sign-in.

Auto-deploy only redeploys when `main` moves, so `.env` changes always need
this manual restart.

| Variable | Example / notes |
| --- | --- |
| `POSTGRES_*` | Compose-only; builds `DATABASE_URL` for the api service |
| `DATABASE_URL` | `postgresql://csgooners:…@db:5432/csgooners` (compose) or `…@127.0.0.1:5432/…` (systemd) |
| `TOKEN_ENCRYPTION_KEYS` | Fernet key(s), newest first. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SESSION_SECRET` | ≥ 32 random chars (needed for uploads too; shorter/missing leaves uploads off) |
| `GUEST_UPLOADS` | default `true`: upload without Steam via a guest session; `false` requires Steam sign-in |
| `STEAM_WEB_API_KEY` | https://steamcommunity.com/dev/apikey, domain `csgooner.com` (step-by-step: [Steam Web API key](#steam-web-api-key)) |
| `PUBLIC_API_URL` | `https://api-site.csgooner.com` (OpenID realm + return URL; must match the public hostname exactly) |
| `FRONTEND_URL` | `https://csgooner.com` (redirect after Steam login) |
| `ALLOWED_ORIGINS` | `https://csgooner.com,https://www.csgooner.com,http://localhost:5173,http://127.0.0.1:5173` |
| `CLOUDFLARE_TUNNEL_TOKEN` | **not needed** on `counterstrike` (host `cloudflared` holds the token); only for the unused compose `--profile tunnel` sidecar |
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
- Host-only cookie on `api-site.csgooner.com` is same-site with `csgooner.com`, so
  leave `SESSION_COOKIE_DOMAIN` unset and `SESSION_COOKIE_SAMESITE=lax`.

## HTTPS / reverse proxy

Pick **one** edge path. Postgres never goes through any of them.

**In use: Cloudflare Tunnel with host `cloudflared`** (Option A). Residential
WAN IPs change, and a static grey-cloud `A` record breaks silently without DDNS.
The Tunnel also avoids opening 80/443 on the house. Tradeoff: Tunnel uploads
are capped at **~100 MB** (see Option A); full demos need the Caddy/nginx fallback.

### Option A: Cloudflare Tunnel, host `cloudflared` (current setup)

How `counterstrike` is set up:

1. In Cloudflare Zero Trust → Networks → Tunnels, a tunnel was created for this
   VM. `cloudflared` was installed **on the Debian host** with the dashboard's
   install script (`cloudflared service install <token>`), so it runs as the
   systemd unit **`cloudflared.service`** and holds the token itself.
2. Public hostname: **`api-site.csgooner.com`** → service **`http://localhost:8000`**.
3. Compose publishes the API on **`127.0.0.1:8000`** (`ports:
   "127.0.0.1:8000:8000"` in
   [`docker-compose.yml`](../deploy/homelab/docker-compose.yml)). That loopback
   port is what host `cloudflared` reaches. Keep it on loopback: don't change it
   to `0.0.0.0` or `8000:8000`.
4. **Don't** start `--profile tunnel`. The compose `cloudflared` sidecar is
   only for a stack with no host install, and it would need the service set to
   `http://api:8000` instead.

Operating the host tunnel:

```sh
systemctl status cloudflared --no-pager
journalctl -u cloudflared -n 50 --no-pager
sudo systemctl restart cloudflared
```

If `cloudflared` logs `connection refused` to `[::1]:8000`, `localhost`
resolved to IPv6 but Docker only published IPv4 loopback. Change the
public-hostname service to `http://127.0.0.1:8000` in the dashboard.

DNS for `api-site` is a proxied CNAME to `<tunnel-id>.cfargotunnel.com`, which
Cloudflare creates when you add the public hostname. No home IP appears in
public DNS.

**Upload limit (hard):** Cloudflare's proxy (including Tunnel) caps request
bodies around **~100 MB**. Full-length CS2 demos are often larger and will get
**413** on this path. Keep `UPLOAD_MAX_BYTES=100000000` (the
[`deploy/homelab/.env.example`](../deploy/homelab/.env.example) default) so the
API rejects oversized bodies before Cloudflare does. For full demos, use
Option B/C (grey-cloud Caddy/nginx) and raise `UPLOAD_MAX_BYTES` to 1 GiB there.
(Grey cloud can't share a hostname with a Tunnel route. A fallback would need
its own DNS-only hostname, or `api-site` moved off the Tunnel.)
Steam share-code sync still works over Tunnel when demos stay under the cap or
are fetched server-side by the bot.

### Option B — Caddy on the VM (`--profile edge`) — fallback

Use when Tunnel limits block large `.dem` uploads. Set
`UPLOAD_MAX_BYTES=1073741824` in `.env` for this path (Caddyfile already allows
1 GiB bodies).

1. Port-forward WAN **80/443** → `192.168.4.54` (or bind the VM to a
   public IP). **Only one host on the WAN IP can own 80/443.** If the lab already
   has a shared edge Caddy for other sites, skip this profile: add an
   `api-site.csgooner.com` site block on that proxy → `http://192.168.4.54:8000`,
   bind/publish the API on the VM LAN interface, and firewall that port to the
   proxy only.
2. Cloudflare DNS for `api-site.csgooner.com`: **A** → current home WAN IP,
   **DNS only (grey cloud)**. Grey cloud **exposes the home public IP** to anyone
   who resolves the name — acceptable for 1 GB uploads, but be aware. Because
   residential IPs are usually **dynamic**, run a DDNS updater (Cloudflare API
   token that upserts the `api-site` A record) or the API will go dark after a lease
   renew.
3. Start Caddy:

```sh
docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env --profile edge up -d
```

[`deploy/homelab/Caddyfile`](../deploy/homelab/Caddyfile) terminates TLS for
`api-site.csgooner.com` and reverse-proxies to the `api` service (1 GiB body limit).
Uvicorn is started with `--proxy-headers --forwarded-allow-ips='*'` so Steam
OpenID return URLs and client IPs see `https` / the real client behind Caddy.

### Option C — nginx on the host — fallback

Terminate TLS on the host and proxy to `http://127.0.0.1:8000`. Same DDNS /
grey-cloud caveats as Option B. Sketch:

```nginx
server {
  listen 443 ssl http2;
  server_name api-site.csgooner.com;
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

## Cloudflare DNS for `api-site.csgooner.com`

| Path | Record | Name | Content | Proxy |
| --- | --- | --- | --- | --- |
| **Current (Tunnel)** | CNAME (auto-created by the Tunnel public hostname) | `api-site` | `<tunnel-id>.cfargotunnel.com` | proxied (required by Tunnel) |
| Fallback (Caddy/nginx) | A | `api-site` (or a separate DNS-only name) | current home WAN IP (+ **DDNS**) | **DNS only** (grey) |

Why grey cloud on the fallback: Cloudflare Free/Pro proxy (and Tunnel) caps
uploads around **100 MB**. Full-length demos exceed that, so the Tunnel default
keeps `UPLOAD_MAX_BYTES=100000000`; grey-cloud Caddy/nginx is the path for 1 GiB
uploads. Keep the Worker for `csgooner.com`; only the API hostname uses grey
cloud when you need large demo uploads.

Grey cloud also publishes the home WAN IP in public DNS. Prefer Tunnel when that
exposure or DDNS churn is undesirable.

The old `api.csgooner.com` CNAME to `*.onrender.com` belongs to the legacy Render
path. The homelab doesn't use it, so delete it once Render is retired.

## Auto-deploy from `main`

`counterstrike` pulls and redeploys the API on its own; merging to `main` is
the deploy. No GitHub token or inbound access is needed (anonymous HTTPS fetch
of the public repo).

| Piece | What it does |
| --- | --- |
| [`cs-analytics-autodeploy.timer`](../deploy/systemd/cs-analytics-autodeploy.timer) | Fires every ~2 min (2 min after boot, then 2 min after each run, ±20 s jitter) |
| [`cs-analytics-autodeploy.service`](../deploy/systemd/cs-analytics-autodeploy.service) | Oneshot; runs `/usr/local/libexec/cs-analytics-autodeploy` as `agent` (in group `docker`) with `REPO_DIR=/opt/cs-analytics`, `BRANCH=main` |
| [`deploy/autodeploy.sh`](../deploy/autodeploy.sh) | `git fetch origin main`; if it differs from the last deployed sha (`/var/lib/cs-analytics-autodeploy/deployed-sha`), `git reset --hard` to it, keeping untracked/ignored files such as `deploy/homelab/.env` (never `git clean`), then runs `deploy/deploy.sh`. If that script is missing it falls back to `compose up -d --build` + a `/health` wait. The sha is recorded only on success, so a failed deploy retries next tick. `flock` prevents overlapping runs |
| [`deploy/deploy.sh`](../deploy/deploy.sh) | `compose up -d --build --remove-orphans` (no profiles), waits up to 180 s for `http://127.0.0.1:8000/health`, dumps `ps` + api logs on failure, then prunes dangling images. Safe to run by hand |
| [`cs-analytics-image-prune.timer`](../deploy/systemd/cs-analytics-image-prune.timer) / [`.service`](../deploy/systemd/cs-analytics-image-prune.service) | Weekly (Sun 04:30 ± 30 min, persistent): `docker image prune -af` + `docker builder prune -f` for anything unused for 7+ days; images used by running containers are kept |

`autodeploy.sh` is installed **outside** the checkout so `git reset` can't
rewrite it mid-run. Install / update:

```sh
cd /opt/cs-analytics
sudo install -m 0755 deploy/autodeploy.sh /usr/local/libexec/cs-analytics-autodeploy
sudo cp deploy/systemd/cs-analytics-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cs-analytics-autodeploy.timer cs-analytics-image-prune.timer
```

Changes to `deploy/autodeploy.sh` or the units in `main` aren't picked up
automatically; re-run the install step above. `deploy/deploy.sh` and everything
else is picked up on the next deploy.

Operate:

```sh
systemctl list-timers 'cs-analytics-*'
journalctl -u cs-analytics-autodeploy -f          # deploy logs
sudo systemctl start cs-analytics-autodeploy      # deploy now instead of waiting
cat /var/lib/cs-analytics-autodeploy/deployed-sha # last good deploy
```

Don't hand-edit tracked files on the VM: the next deploy resets them. Keep
local config in `deploy/homelab/.env` (ignored).

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
`https://api-site.csgooner.com`.

1. In GitHub, set repo variable `VITE_API_BASE_URL=https://api-site.csgooner.com`
   or delete it (Settings → Secrets and variables → Actions → Variables).
   **If it's still set to `https://api.csgooner.com`, it overrides the new
   default and the site will call the wrong host.**
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
7. Prefer Cloudflare Tunnel (host `cloudflared` → `http://localhost:8000`); otherwise Caddy/nginx
   with DDNS. Schedule [`backup-pg.sh`](../deploy/homelab/backup-pg.sh) via the
   timer units or host cron against local `pg_dump`.

## Cutover checklist

1. Secrets in `deploy/homelab/.env` on `counterstrike` (or `/etc/cs-analytics/api.env`),
   including `STEAM_WEB_API_KEY` ([guide](#steam-web-api-key)) and
   `PUBLIC_API_URL=https://api-site.csgooner.com`.
2. Compose (or systemd) up **without** `--profile tunnel`; `/health` OK on
   `127.0.0.1:8000`; confirm a dump appeared under `csa-pg-backups`.
3. Host `cloudflared.service` active; Tunnel public hostname
   `api-site.csgooner.com` → `http://localhost:8000`.
4. HTTPS working: `curl https://api-site.csgooner.com/health`.
5. GitHub `VITE_API_BASE_URL` = `https://api-site.csgooner.com` (or unset) + frontend redeploy.
6. Browser: csgooner.com → Steam sign-in → upload or sync.
7. Player auth + share codes (see [`docs/live-e2e-checklist.md`](live-e2e-checklist.md)).

Live go-live steps beyond this page:
[`docs/live-e2e-checklist.md`](live-e2e-checklist.md). Legacy Render path (not
baseline): [`docs/deploy-render.md`](deploy-render.md).
