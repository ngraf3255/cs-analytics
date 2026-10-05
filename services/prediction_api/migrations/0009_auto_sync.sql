-- Automatic background sync (steamlink.autosync), Leetify-style: linked users get
-- new matches pulled without pressing Sync.
--
-- users.auto_sync_enabled: the user's opt-out toggle (1 = on, the default).
-- sync_state.last_synced_at: when a sync (manual or automatic) last finished OK.
-- sync_state.next_auto_sync_at: when the scheduler may sync this user next
--   (NULL: due once last_finished_at is older than AUTO_SYNC_INTERVAL_SECONDS).
--   Also the per-user claim: a scheduler moves it forward (compare-and-set)
--   before syncing, so two API processes never auto-sync the same user twice.
-- sync_state.auto_sync_failures: consecutive failed syncs (exponential backoff).
-- sync_state.last_auto_sync_at / last_auto_sync_error: the last automatic run.
-- scheduler_leases: one row per periodic task; only the holder of an unexpired
--   lease runs a tick, so several processes share one global per-tick user cap.
-- Portable SQL: runs on PostgreSQL and SQLite.

ALTER TABLE users ADD COLUMN auto_sync_enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE sync_state ADD COLUMN last_synced_at TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN next_auto_sync_at TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN auto_sync_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_state ADD COLUMN last_auto_sync_at TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN last_auto_sync_error TEXT;
UPDATE sync_state SET last_synced_at = last_finished_at WHERE status = 'ok';
CREATE INDEX sync_state_next_auto_idx ON sync_state (next_auto_sync_at);

CREATE TABLE scheduler_leases (
    name TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
