-- Cross-source dedupe: the same match can arrive by manual upload and by
-- Steam sync (either order). Store it once per user, keyed by any of:
--   * share_code      (already UNIQUE per user; uploads without one use 'upload:<sha256>')
--   * valve_match_id  (decoded from the share code; 'upload' = unknown, excluded)
--   * demo_sha256     (SHA-256 of the decompressed .dem; NULL = not downloaded)
-- Portable SQL: runs on PostgreSQL and SQLite (partial indexes need SQLite 3.8+).

ALTER TABLE matches ADD COLUMN source TEXT NOT NULL DEFAULT 'steam_sync';
ALTER TABLE matches ADD COLUMN demo_sha256 TEXT;

-- Uploads stored before this migration carried their hash in the share code.
UPDATE matches SET source = 'upload', demo_sha256 = SUBSTR(share_code, 8) WHERE share_code LIKE 'upload:%';

CREATE UNIQUE INDEX matches_user_demo_sha256_key ON matches (user_id, demo_sha256);
CREATE UNIQUE INDEX matches_user_valve_match_key ON matches (user_id, valve_match_id) WHERE valve_match_id <> 'upload';
