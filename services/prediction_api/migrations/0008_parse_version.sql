-- matches.parse_version: what the parse that stored the match's rounds extracted
-- (steamlink.storage.base.PARSE_VERSION). 0 = parsed before per-player rounds were
-- recorded (players_recorded = 0) or a stub; 1 = per-player rounds recorded but
-- warmup / knife rounds before the last begin_new_match still counted in the
-- all-player rounds (and rounds_count), rounds numbered from the demo start;
-- 2 = those rounds left out everywhere, numbered from the match start.
--
-- No data is rewritten here: demos are not kept on the server and a version-1
-- match's warmup rounds cannot be told apart reliably from its stored rows, so
-- older matches are only flagged (API: match "outdated"); re-uploading the same
-- demo re-parses it and replaces its rounds and player rounds.
-- upload_jobs.match_updated: 1 when the job re-parsed such an outdated match and
-- replaced its rounds (the UI says "updated" rather than "already in your list").
-- Portable SQL: runs on PostgreSQL and SQLite.

ALTER TABLE matches ADD COLUMN parse_version INTEGER NOT NULL DEFAULT 0;
UPDATE matches SET parse_version = 1 WHERE players_recorded = 1 AND status = 'imported';
ALTER TABLE upload_jobs ADD COLUMN match_updated INTEGER;
