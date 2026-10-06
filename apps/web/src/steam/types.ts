/** The last sync failed in a way only new codes fix (GET /me). */
export type NeedsRelink = {
  reason: "invalid_known_code" | "invalid_auth_code" | "credentials_unreadable" | string;
  /** Which code to replace: the share code (auth code kept) or the auth code. */
  field: "share_code" | "auth_code" | string;
};

export type MatchAccess = {
  linked: boolean;
  auth_code_hint: string | null;
  linked_at: string | null;
  updated_at: string | null;
  /** Older APIs don't send it. */
  needs_relink?: NeedsRelink | null;
  /** Auth code saved without a share code yet (linked before a recent match): sync starts once one is
   * added. Older APIs don't send it. */
  awaiting_share_code?: boolean;
};

export type SyncStatus = {
  status: "idle" | "running" | "ok" | "error" | string;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_error: string | null;
  last_imported_count: number;
  /** Matches queued by syncs that are still downloading / parsing in the background. */
  active_jobs?: number;
  /** Last sync (manual or automatic) that finished OK. Missing from older APIs. */
  last_synced_at?: string | null;
  /** Automatic background sync. Missing from older APIs. */
  auto_sync?: AutoSync;
};

/** Automatic background sync (GET /steam/sync, /me sync.auto_sync; PUT /steam/auto-sync). */
export type AutoSync = {
  /** The user's toggle (on by default). */
  enabled: boolean;
  /** It will actually run for this user; else paused_reason says why. */
  active: boolean;
  paused_reason: "turned_off" | "not_linked" | "needs_share_code" | "needs_relink" | "server_disabled" | "demo_retrieval_not_configured" | string | null;
  /** How often linked users are synced (null: off on this server). */
  interval_seconds: number | null;
  /** Earliest time of the next automatic sync (null while paused). */
  next_at: string | null;
  last_run_at: string | null;
  /** Error of the last automatic run (null: it worked). */
  last_error: string | null;
  /** Consecutive failed syncs (the server backs off). */
  failures: number;
};

/** GET /steam/sync: status plus the user's most recent sync jobs, newest first. */
export type SyncState = SyncStatus & { jobs: UploadJob[] };

export type Me = {
  /** SteamID64; null for a guest account (POST /auth/guest: upload + reports without Steam). */
  steam_id: string | null;
  /** Missing on older APIs (always Steam then). */
  account?: "steam" | "guest";
  created_at: string;
  match_access: MatchAccess;
  sync: SyncStatus;
};

/** POST /steam/sync: the request only walks the match history; each new match is a background job. */
export type SyncResult = {
  status: "up_to_date" | "partial" | "queue_full" | "error" | string;
  queued: number;
  /** New matches already in the user's list (no download). */
  skipped: number;
  /** New matches already imported on the server (e.g. by another player in the match): added to the
   * user's list right away, no download. Missing from older API versions. */
  attached?: number;
  processed: number;
  has_more: boolean;
  error: string | null;
  jobs: UploadJob[];
};

export type MatchSummary = {
  id: string;
  source: "steam_sync" | "upload" | string;
  share_code: string | null;
  status: "imported" | "unavailable" | "parse_failed" | string;
  status_reason: string | null;
  map_name: string | null;
  rounds_count: number;
  /** When the match was added to this user's list. */
  imported_at: string;
  /** When the match was played if known (Steam sync: Valve's Game Coordinator), else imported_at;
   * date_source says which. Missing from older API versions (use imported_at). */
  date?: string;
  date_source?: "played" | "imported";
  played_at?: string | null;
  /** Final score: rounds won by the team on each side at the end; null if the demo didn't say.
   * Missing from older API versions. */
  score?: { ct: number; t: number } | null;
  /** Per-player sides / stats were recorded at parse time (false: parsed before that; re-upload). */
  players_recorded?: boolean;
  /** Parsed by an older parser (null: up to date): "players_not_recorded" (no per-player stats) or
   * "parser_updated" (e.g. warmup / knife rounds may still be counted). The server doesn't keep
   * demos, so the fix is always to upload the same demo again. Missing from older APIs. */
  outdated?: { reason: "players_not_recorded" | "parser_updated" | string; fix: "reupload" | string } | null;
  degraded?: { reason: string; detail?: string } | null;
};

/** A demo being imported in the background: a manual upload (POST /matches/upload) or one match
 * of a Steam sync (POST /steam/sync). Poll GET /matches/upload/{id} or GET /steam/sync. */
export type UploadJob = {
  id: string;
  kind?: "upload" | "steam_sync" | string;
  share_code?: string | null;
  status: "queued" | "processing" | "done" | "failed" | string;
  stage: "locating" | "downloading" | "decompressing" | "hashing" | "parsing" | "storing" | string | null;
  progress: number | null;
  queue_position: number | null;
  error: string | null;
  size_bytes: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  match: MatchSummary | null;
  created: boolean | null;
  /** The match was stored by an older parser and this job re-parsed it (now up to date). */
  updated?: boolean;
  attempts?: number;
};

export type RoundReport = {
  round_number: number;
  actual_winner: "ct" | "t" | null;
  opening_kill: { side: "ct" | "t" | null; seconds: number | null; weapon: string | null } | null;
  prediction: {
    predicted_winner: "ct" | "t";
    probabilities: { ct: number; t: number };
  } | null;
  unscored_reason: string | null;
  /** The signed-in player's round (null: not in it, or not recorded). Missing from older APIs. */
  you?: {
    side: "ct" | "t";
    won: boolean | null;
    kills: number;
    deaths: number;
    opening_kill: boolean;
    opening_death: boolean;
    survived: boolean;
    /** The model's probability for the player's side (null when the round is unscored). */
    win_probability: number | null;
  } | null;
};

export type MatchResult = { score: { you: number; them: number } | null; result: "won" | "lost" | "tied" | null };

/** The signed-in player in one match. ``not_in_match``: e.g. an uploaded pro demo; ``unknown``:
 * parsed before per-player rounds were recorded (re-upload the demo). */
export type YouInMatch =
  | { status: "not_in_match" | "unknown"; steam_id: string }
  | (MatchResult & {
    status: "in_match";
    steam_id: string;
    first_side: "ct" | "t";
    last_side: "ct" | "t";
    rounds: number;
    won: number;
    win_rate: number | null;
    kills: number;
    deaths: number;
    kd: number | null;
    opening_kills: number;
    opening_deaths: number;
    survived: number;
  });

/** One player of a match (GET /matches/{id} ``players``). ``team`` is relative to the signed-in
 * player when they're in the demo, else the side the player started on. */
export type MatchPlayer = {
  steam_id: string;
  team: "you" | "teammate" | "opponent" | "ct_start" | "t_start" | string;
  rounds: number;
  first_side: "ct" | "t" | null;
  kills: number;
  deaths: number;
  kd: number | null;
  kills_per_round: number | null;
  opening_kills: number;
  opening_deaths: number;
  survived: number;
  /** Other matches in your list with this player on your team / the other one. */
  history?: { matches_with: number; matches_against: number };
};

export type MatchReport = {
  match: MatchSummary;
  model: { calibrated_for_matchmaking: boolean; note: string };
  summary: { rounds: number; scored: number; correct_predictions: number };
  rounds: RoundReport[];
  /** Missing from older API versions. */
  you?: YouInMatch;
  /** Everyone in the demo; missing from older APIs, empty when not recorded. */
  players?: MatchPlayer[];
};

/** Model hit rate / Brier score over a set of scored rounds (null when none were scored). */
export type ModelTally = { scored_rounds: number; correct: number; hit_rate: number | null; brier_score: number | null };
export type SideTally = {
  rounds_with_winner: number;
  ct_won: number;
  t_won: number;
  ct_win_rate: number | null;
  t_win_rate: number | null;
};
export type Conversion = { rounds: number; converted: number; conversion_rate: number | null };

export type SideRate = { rounds: number; won: number; win_rate: number | null };
export type Results = { won: number; lost: number; tied: number; unknown: number };
/** The signed-in player's own rounds over a set of matches. */
export type PlayerTally = {
  matches: number;
  rounds: number;
  rounds_with_winner: number;
  won: number;
  win_rate: number | null;
  results: Results;
  kills: number;
  deaths: number;
  kd: number | null;
  kills_per_round: number | null;
  survived: number;
  survival_rate: number | null;
};
export type PlayerWindow = { matches: number; rounds: number; won: number; win_rate: number | null; kd: number | null; results: Results };

/** GET /matches/summary ``you``: the signed-in player's own rounds (their side each round). */
export type YouAnalytics = PlayerTally & {
  steam_id: string;
  /** Demos the player is not in (e.g. uploaded pro matches): left out of these numbers. */
  matches_without_you: number;
  /** Parsed before per-player rounds were recorded: left out (re-upload to include). */
  matches_unknown: number;
  sides: { ct: SideRate; t: SideRate };
  opening_duels: {
    taken: number; won: number; lost: number; win_rate: number | null;
    round_win_rate_after_opening_kill: number | null; round_win_rate_after_opening_death: number | null;
  };
  maps: (PlayerTally & { map_name: string | null; sides: { ct: SideRate; t: SideRate } })[];
  recent_form: {
    window: number;
    recent: PlayerWindow;
    earlier: PlayerWindow;
    win_rate_change: number | null;
    kd_change: number | null;
    matches: (MatchResult & {
      id: string; map_name: string | null; date: string; date_source: "played" | "imported"; played_at: string | null;
      first_side: "ct" | "t"; rounds: number; won: number; win_rate: number | null; kills: number; deaths: number;
    })[];
  };
};

/** GET /matches/summary: analytics across every match in the user's list. ``you`` is the signed-in
 * player's own rounds; everything else covers all players in the matches (CT / T = map sides);
 * rates are 0..1 or null. */
export type MatchesAnalytics = {
  model: { calibrated_for_matchmaking: boolean; coin_flip_brier_score: number; note: string };
  totals: {
    matches: number;
    imported_matches: number;
    not_imported_matches: number;
    /** Imported matches parsed by an older parser (included as stored; re-upload to update). */
    outdated_matches?: number;
    rounds: number;
    rounds_with_winner: number;
    scored_rounds: number;
    unscored_rounds: number;
    first_imported_at: string | null;
    last_imported_at: string | null;
  };
  prediction: ModelTally & {
    /** Hit rate of always backing the side that got the opening kill (same rounds). */
    opening_kill_baseline_hit_rate: number | null;
    calibration: { min: number; max: number; rounds: number; mean_confidence: number | null; hit_rate: number | null }[];
  };
  sides: SideTally;
  opening_kills: Conversion & {
    average_seconds: number | null;
    by_side: { ct: Conversion; t: Conversion };
    top_weapons: (Conversion & { weapon: string })[];
  };
  maps: (SideTally & ModelTally & { map_name: string | null; matches: number; rounds: number })[];
  unscored_reasons: { reason: string; rounds: number }[];
  /** Missing from older API versions. */
  you?: YouAnalytics;
  scope?: Record<string, "all_players" | "signed_in_player">;
  recent_form: {
    window: number;
    recent: ModelTally & { matches: number; ct_win_rate: number | null };
    earlier: ModelTally & { matches: number; ct_win_rate: number | null };
    /** recent.hit_rate - earlier.hit_rate; null unless both windows have scored rounds. */
    hit_rate_change: number | null;
    matches: (ModelTally & { id: string; map_name: string | null; imported_at: string; rounds: number;
      score: { ct: number; t: number } | null })[];
  };
};

/** GET /steam/status. ``enabled``: Steam sign-in + sync (older APIs only send this one);
 * ``upload``: demo upload + match reports; ``guest``: upload without Steam (POST /auth/guest). */
export type ServerStatus = {
  enabled: boolean;
  steam?: boolean;
  upload?: boolean;
  guest?: boolean;
};
