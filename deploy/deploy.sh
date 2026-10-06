#!/usr/bin/env bash
# Build and (re)start the homelab stack from the current checkout, then wait
# for the API to report healthy. Run from anywhere; resolves the repo root from
# this script's location. Called by deploy/autodeploy.sh after it resets the
# checkout to origin/main, and safe to run by hand:
#   /opt/cs-analytics/deploy/deploy.sh
# Never starts compose profiles (no tunnel sidecar, no Caddy): host cloudflared
# reaches the api on 127.0.0.1:8000.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${REPO_DIR}/deploy/homelab/docker-compose.yml"
ENV_FILE="${REPO_DIR}/deploy/homelab/.env"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-180}"

log() { echo "deploy: $*"; }

if [[ ! -f "$ENV_FILE" ]]; then
  echo "deploy: missing ${ENV_FILE} (copy deploy/homelab/.env.example and fill it in)" >&2
  exit 1
fi

compose() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

sha="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
log "building and starting stack at ${sha}"
compose up -d --build --remove-orphans

log "waiting up to ${HEALTH_TIMEOUT_SECONDS}s for ${HEALTH_URL}"
deadline=$(( SECONDS + HEALTH_TIMEOUT_SECONDS ))
until curl -fsS -m 5 "$HEALTH_URL" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "deploy: API not healthy after ${HEALTH_TIMEOUT_SECONDS}s at ${sha}" >&2
    compose ps >&2 || true
    compose logs --tail 50 api >&2 || true
    exit 1
  fi
  sleep 3
done
log "healthy at ${sha}"

# Drop dangling images left by the rebuild (tagged/in-use images are kept).
docker image prune -f >/dev/null || true
log "done"
