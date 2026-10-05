-- Steam-linked match sync schema.
-- Written in portable SQL so the same migration runs on PostgreSQL (production)
-- and SQLite (tests / local development).

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    steam_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Server-side sessions. Only a SHA-256 hash of the cookie token is stored.
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX sessions_user_id_idx ON sessions (user_id);

-- The CS2 match-history authentication code is stored only as authenticated
-- ciphertext. cursor_share_code is the last processed match sharing code.
CREATE TABLE match_access (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    auth_code_ciphertext TEXT NOT NULL,
    auth_code_last4 TEXT NOT NULL,
    cursor_share_code TEXT NOT NULL,
    consented_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE sync_state (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    lock_token TEXT,
    lock_expires_at TIMESTAMPTZ,
    last_started_at TIMESTAMPTZ,
    last_finished_at TIMESTAMPTZ,
    last_error TEXT,
    last_imported_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE matches (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    share_code TEXT NOT NULL,
    valve_match_id TEXT NOT NULL,
    status TEXT NOT NULL,
    status_reason TEXT,
    map_name TEXT,
    rounds_count INTEGER NOT NULL DEFAULT 0,
    imported_at TIMESTAMPTZ NOT NULL,
    UNIQUE (user_id, share_code)
);
CREATE INDEX matches_user_imported_idx ON matches (user_id, imported_at);

CREATE TABLE rounds (
    match_id TEXT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    winner_side TEXT,
    opening_kill_side TEXT,
    opening_kill_seconds DOUBLE PRECISION,
    opening_weapon TEXT,
    unscored_reason TEXT,
    PRIMARY KEY (match_id, round_number)
);
