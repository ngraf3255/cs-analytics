-- Steam sync downloads and parses demos on the same background worker as
-- uploads (steamlink.jobs), so POST /steam/sync only walks the share-code
-- history and queues one job per new match.
--   kind: upload | steam_sync
--   steam_sync jobs: share_code is the match to import; demo_path is where the
--     worker downloads the demo (the file only exists while the job runs, so a
--     restart re-downloads instead of failing the job).
-- One sync job per user and share code: syncing the same match again reuses
-- (re-queues) its row instead of adding another.
-- Portable SQL: runs on PostgreSQL and SQLite (partial indexes need SQLite 3.8+).

ALTER TABLE upload_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'upload';
CREATE UNIQUE INDEX upload_jobs_user_sync_share_key ON upload_jobs (user_id, share_code) WHERE kind = 'steam_sync';
CREATE INDEX upload_jobs_user_kind_created_idx ON upload_jobs (user_id, kind, created_at);
