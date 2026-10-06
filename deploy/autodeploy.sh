#!/usr/bin/env bash
# Pull-based auto-deploy for the homelab VM. Installed OUTSIDE the checkout
# (/usr/local/libexec/cs-analytics-autodeploy) so `git reset` can't rewrite it
# mid-run; run by cs-analytics-autodeploy.service on a timer.
# Fetches origin/main over anonymous https (public repo; no token on the VM).
# If origin/main differs from the last successfully deployed sha, it hard-resets
# the checkout (untracked/ignored files such as deploy/homelab/.env are kept;
# never `git clean`) and runs the repo's deploy/deploy.sh.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/cs-analytics}"
BRANCH="${BRANCH:-main}"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/cs-analytics-autodeploy}"
STATE_FILE="${STATE_DIR}/deployed-sha"
LOCK_FILE="${STATE_DIR}/lock"

mkdir -p "$STATE_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "autodeploy: another run holds the lock; skipping"
  exit 0
fi

cd "$REPO_DIR"
export GIT_TERMINAL_PROMPT=0
git fetch --quiet origin "$BRANCH"
target="$(git rev-parse "origin/${BRANCH}")"
deployed="$(cat "$STATE_FILE" 2>/dev/null || true)"

if [[ "$target" == "$deployed" ]]; then
  exit 0
fi

echo "autodeploy: origin/${BRANCH} ${target:0:12} != deployed ${deployed:0:12}; updating"
git reset --hard --quiet "$target"

if [[ -x deploy/deploy.sh ]]; then
  # Run (not exec) so the sha is recorded only after deploy.sh succeeds;
  # a failed deploy is retried on the next tick. fd 9 keeps the lock held.
  deploy/deploy.sh
else
  echo "autodeploy: deploy/deploy.sh missing; falling back to compose up --build"
  docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env up -d --build
  for _ in $(seq 60); do curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1 && break; sleep 3; done
  curl -fsS -m 5 http://127.0.0.1:8000/health >/dev/null
fi
echo "$target" > "$STATE_FILE"
echo "autodeploy: deployed ${target:0:12}"
