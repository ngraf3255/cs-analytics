-- Per-player rounds: the side (CT / T) every player (SteamID64) was on in each
-- round, and their kills, deaths, opening kill / death and survival
-- (steamlink.demo_parser.extract_player_rounds). Stored for every player in the
-- demo, not per owner: a shared match (match_owners) is analysed for each owner
-- from their own SteamID, including owners who attach it later without a parse.
--
-- matches.players_recorded: 1 when the parse recorded player rounds (even if the
-- owner is not in the demo), 0 = unknown (parsed before this migration, or a
-- stub). No backfill here (demos are not kept); re-uploading the demo fills it in.
-- matches.played_at: when the match was played, if known (Steam sync: the Game
-- Coordinator's match time; played_at_source = 'valve_gc'); NULL otherwise
-- (CS2 demos carry no date) and the UI falls back to imported_at, labelled.
-- Portable SQL: runs on PostgreSQL and SQLite.

CREATE TABLE player_rounds (
    match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    steam_id TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    side TEXT NOT NULL,
    kills INTEGER NOT NULL DEFAULT 0,
    deaths INTEGER NOT NULL DEFAULT 0,
    opening_kill INTEGER NOT NULL DEFAULT 0,
    opening_death INTEGER NOT NULL DEFAULT 0,
    survived INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (match_id, steam_id, round_number)
);
CREATE INDEX player_rounds_steam_idx ON player_rounds (steam_id, match_id);

ALTER TABLE matches ADD COLUMN players_recorded INTEGER NOT NULL DEFAULT 0;
ALTER TABLE matches ADD COLUMN played_at TIMESTAMPTZ;
ALTER TABLE matches ADD COLUMN played_at_source TEXT;
