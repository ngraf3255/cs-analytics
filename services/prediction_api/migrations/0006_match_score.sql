-- Final score for the match report header: rounds won by the team on the CT /
-- T side at the end of the demo (steamlink.demo_parser.final_score). NULL for
-- stubs (no demo) and for matches parsed before this migration (re-upload or
-- re-sync fills it in).
-- Portable SQL: runs on PostgreSQL and SQLite.

ALTER TABLE matches ADD COLUMN score_ct INTEGER;
ALTER TABLE matches ADD COLUMN score_t INTEGER;
