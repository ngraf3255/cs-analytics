# Deploy baseline: homelab API + Postgres

**Chosen baseline (2026-10-05):** run FastAPI and Postgres together on a home
Proxmox VM. Site stays on Cloudflare; API is public at `api.csgooner.com`.

| Piece | Where |
| --- | --- |
| Frontend | Cloudflare Worker (`csgooner.com`) |
| API + Postgres | Homelab Proxmox VM: **2 vCPU** (R5 5600X host), **8 GB RAM** |
| Public API | `https://api.csgooner.com` → home (HTTPS termination at the edge or on the VM) |

## Why not Render Postgres / remote DB

- Render managed Postgres is paid; skipped.
- Do **not** open `5432` to the public internet for a remote API.
- API and DB share the same VM/LAN, so Postgres stays private (localhost or
  private network only).

## Capacity note

Overnight measurement on a tighter free-shape (0.1 CPU / 512 MB): a 441 MB
`.dem` finished in ~53 s. 2 cores + 8 GB is enough headroom for API + Postgres
and demo parse jobs.

## Point DNS at home

1. Change `api.csgooner.com` from the old Render CNAME to the home public IP
   (or a tunnel hostname). Prefer Cloudflare proxy (orange cloud) only if
   upload size / timeout limits are acceptable; otherwise DNS-only + cert on
   the VM (or Cloudflare Tunnel).
2. Set GitHub variable `VITE_API_BASE_URL=https://api.csgooner.com` and redeploy
   the frontend Worker.
3. Smoke: `curl https://api.csgooner.com/health` → `{"status":"ok"}`.

Env vars and migrate/start commands are the same as
[`docs/deploy-render.md`](deploy-render.md) (that doc is the old Render path;
keep it for reference only). Live go-live steps:
[`docs/live-e2e-checklist.md`](live-e2e-checklist.md).
