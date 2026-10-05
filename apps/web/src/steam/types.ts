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
};

export type Me = {
  steam_id: string;
  created_at: string;
  match_access: MatchAccess;
  sync: SyncStatus;
};

export type SyncResult = {
  status: "up_to_date" | "partial" | "demo_not_ready" | "error" | string;
  imported: number;
  processed: number;
  has_more: boolean;
  error: string | null;
};

export type MatchSummary = {
  id: string;
  share_code: string;
  status: "imported" | "unavailable" | "parse_failed" | string;
  status_reason: string | null;
  map_name: string | null;
  rounds_count: number;
  imported_at: string;
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
