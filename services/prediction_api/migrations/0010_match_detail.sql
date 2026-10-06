-- Match detail (PARSE_VERSION 3, steamlink.demo_parser): how each round ended and,
-- per player and round, the equipment value at freeze end (their buy) and clutches.
--
-- rounds.end_reason: the round_end reason as demoparser2 names it (t_killed,
-- ct_killed, bomb_exploded, bomb_defused, target_saved, t_surrender, ...).
-- player_rounds.equip_value: current_equip_value at the round's freeze end.
-- player_rounds.clutch_vs: enemies alive when the player became the last one alive
-- on their team (0 = no clutch).
-- All NULL for matches parsed before (demos are not kept, no backfill): those keep
-- their stats and are NOT flagged outdated; re-uploading the demo fills these in.
-- Portable SQL: runs on PostgreSQL and SQLite.

ALTER TABLE rounds ADD COLUMN end_reason TEXT;
ALTER TABLE player_rounds ADD COLUMN equip_value INTEGER;
ALTER TABLE player_rounds ADD COLUMN clutch_vs INTEGER;
