#!/usr/bin/env bash
# Nightly (or on-demand) pg_dump of the compose Postgres into /backups.
# Used by the db-backup compose service; also runnable on the host:
#   docker compose -f deploy/homelab/docker-compose.yml --env-file deploy/homelab/.env \
#     exec -T db-backup /usr/local/bin/backup-pg.sh
set -euo pipefail

PGHOST="${PGHOST:-db}"
PGPORT="${PGPORT:-5432}"
PGUSER="${POSTGRES_USER:-csgooners}"
PGDATABASE="${POSTGRES_DB:-csgooners}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

if [[ -z "${PGPASSWORD:-}" ]]; then
  echo "backup-pg: PGPASSWORD (or POSTGRES_PASSWORD exported as PGPASSWORD) is required" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="${BACKUP_DIR}/csgooners-${stamp}.sql.gz"
tmp="${out}.partial"

echo "backup-pg: dumping ${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE} -> ${out}"
pg_dump \
  --host="$PGHOST" \
  --port="$PGPORT" \
  --username="$PGUSER" \
  --dbname="$PGDATABASE" \
  --no-owner \
  --no-acl \
  --format=plain \
  | gzip -c > "$tmp"
mv -f "$tmp" "$out"
bytes="$(wc -c < "$out" | tr -d ' ')"
echo "backup-pg: wrote ${out} (${bytes} bytes)"

# Prune old dumps kept outside the Postgres data volume.
if [[ "$BACKUP_KEEP_DAYS" =~ ^[0-9]+$ ]] && [[ "$BACKUP_KEEP_DAYS" -gt 0 ]]; then
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'csgooners-*.sql.gz' -mtime "+${BACKUP_KEEP_DAYS}" -print -delete \
    || true
fi
