export type MatchAccess = {
  linked: boolean;
  auth_code_hint: string | null;
  linked_at: string | null;
  updated_at: string | null;
};

export type SyncStatus = {
  status: "idle" | "running" | "ok" | "error" | string;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_error: string | null;
  last_imported_count: number;
  /** Matches queued by syncs that are still downloading / parsing in the background. */
  active_jobs?: number;
};

/** GET /steam/sync: status plus the user's most recent sync jobs, newest first. */
export type SyncState = SyncStatus & { jobs: UploadJob[] };

export type Me = {
  steam_id: string;
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
  /** When the match was added to this user's list (demos carry no match date). */
  imported_at: string;
  /** Final score: rounds won by the team on each side at the end; null if the demo didn't say.
   * Missing from older API versions. */
  score?: { ct: number; t: number } | null;
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
};

export type MatchReport = {
  match: MatchSummary;
  model: { calibrated_for_matchmaking: boolean; note: string };
  summary: { rounds: number; scored: number; correct_predictions: number };
  rounds: RoundReport[];
};
