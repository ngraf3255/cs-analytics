-- Background parse jobs for manual demo uploads. POST /matches/upload stores the
-- file on local disk and returns a job id; one in-process worker parses jobs in
-- order (sharing the parse slot with Steam sync). On the free 0.1 CPU plan a
-- full-length demo takes ~1 minute and a .dem.bz2 several minutes, too long
-- for one HTTP request.
--   status: queued | processing | done | failed
--   stage (while processing): decompressing | hashing | parsing | storing
--   demo_path: local file (Render's disk is ephemeral: after a restart the file
--     is gone and the job is marked failed with error 'server_restarted')
--   demo_sha256: SHA-256 computed while receiving a plain .dem (NULL for .bz2)
--   match_created: 1 = new match, 0 = the demo was already stored (dedupe)
-- Portable SQL: runs on PostgreSQL and SQLite.

CREATE TABLE upload_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    stage TEXT,
    progress REAL,
    share_code TEXT,
    demo_path TEXT NOT NULL,
    demo_sha256 TEXT,
    size_bytes BIGINT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    match_id TEXT,
    match_created INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX upload_jobs_status_created_idx ON upload_jobs (status, created_at);
CREATE INDEX upload_jobs_user_created_idx ON upload_jobs (user_id, created_at);
