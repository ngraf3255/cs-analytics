const MESSAGES: Record<string, string> = {
  steam_sync_disabled: "Steam linking is not available on this deployment yet.",
  steam_sign_in_required: "Sign in through Steam to link and sync your match history.",
  guest_uploads_disabled: "Uploading without Steam isn’t available on this server.",
  not_authenticated: "Sign in to continue.",
  csrf_header_missing: "Your browser blocked the request. Refresh and try again.",
  origin_not_allowed: "This site is not allowed to talk to the API.",
  consent_required: "Confirm you understand before storing your match-history code.",
  invalid_auth_code_format: "That Game Authentication Code does not look right. Use the format ABCD-EFGHI-JKLM.",
  invalid_share_code_format: "That match sharing code does not look right. Paste a CSGO-… code from CS2.",
  invalid_auth_code: "Valve rejected that Game Authentication Code. It may be revoked or mistyped: copy it again from Valve’s page.",
  invalid_share_code:
    "Valve didn’t accept that share code. Use one of your own Competitive, Premier or Wingman matches from the last 30 days, copied exactly.",
  auth_code_is_share_code: "That’s a match sharing code (CSGO-…). Put it in the share-code box; the authentication code looks like ABCD-EFGHI-JKLM.",
  share_code_is_auth_code: "That’s your Game Authentication Code. The share-code box needs a code that starts with CSGO-.",
  auth_code_required: "Paste your Game Authentication Code (ABCD-EFGHI-JKLM) from Valve’s page.",
  share_code_required: "Paste a match sharing code (CSGO-…) or a new Game Authentication Code to update.",
  needs_share_code:
    "Add a share code in Account settings after your next match.",
  valve_rate_limited: "Valve is rate-limiting requests. Wait a minute and try again.",
  valve_unavailable: "Valve’s match-history service did not respond. Try again shortly.",
  api_unreachable: "Can’t reach the cs-analytics server. If it was idle it may be waking up; try again in a minute.",
  not_linked: "Link your match history in Account settings first.",
  already_running: "A sync is already running.",
  too_soon: "Wait about 30 seconds between syncs.",
  match_not_found: "That match is no longer available.",
  demo_retrieval_not_configured:
    "Match sync is wired up, but demo download is not configured on the server yet (needs a Game Coordinator bot). Your share-code cursor was not advanced.",
  demo_bot_auth_failed:
    "The server’s Steam demo bot could not sign in, so demos can’t be fetched right now. Your next sync retries the match.",
  demo_too_large: "That demo is larger than the server allows.",
  not_a_cs2_demo: "That file isn’t a CS2 demo (.dem or .dem.bz2). CS:GO demos aren’t supported.",
  demo_parse_failed: "The demo could not be parsed. It may be damaged; if it plays fine in CS2, try again later.",
  demo_truncated:
    "That demo file is incomplete (it’s cut off). Let CS2 finish saving it, or copy / download it again, then upload it.",
  demo_format_unsupported:
    "Our demo parser can’t read this demo yet: it uses a format from a newer CS2 update or a third-party server (e.g. FACEIT). Try again after the next parser update.",
  demo_parse_timeout: "The demo took too long to parse. Try again in a few minutes.",
  upload_busy: "The server is parsing another demo. Try again in a minute.",
  upload_queue_full: "The server already has several demos waiting to be parsed. Try again in a few minutes.",
  server_restarted: "The server restarted before it finished your demo. Upload it again.",
  internal_error: "The server hit an unexpected error while processing your demo. Try again.",
  demo_not_ready: "That match’s demo isn’t on Valve’s servers yet. Sync again later to retry it.",
  sync_queue_full: "The server already has several demos waiting. Sync again in a few minutes to continue.",
  upload_job_not_found: "That upload is no longer tracked. Check your matches below.",
  demo_has_no_rounds: "That demo has no completed rounds to analyse.",
  upload_network_error: "The upload was interrupted. Check your connection and try again.",
  not_a_demo_file: "Choose a CS2 demo file ending in .dem or .dem.bz2.",
  invalid_auth_code_status: "Valve rejected your saved Game Authentication Code. Paste your current one in the account box above.",
  invalid_known_code: "Your saved share code is no longer valid (Valve only accepts codes from the last 30 days). Paste a recent one in the account box above.",
  rate_limited: "Valve is rate-limiting sync. Try again in a few minutes.",
  valve_error: "Valve returned an unexpected error while syncing.",
  credentials_unreadable: "Stored credentials could not be decrypted. Re-link your codes.",
  cursor_changed: "Your share-code cursor changed during sync. Try again.",
  no_opening_kill: "No opening kill recorded in this round.",
  opening_kill_side_unknown: "Opening kill side could not be determined.",
  opening_kill_before_freeze_end: "Opening kill landed before freeze time ended.",
  freeze_end_missing: "Freeze-end tick was missing from the demo.",
  winner_unknown: "Round winner could not be determined.",
  opening_weapon_unknown: "Opening weapon could not be determined.",
  map_not_in_model: "This map is not in the trained model vocabulary.",
  weapon_not_in_model: "This opening weapon is not in the trained model vocabulary.",
  opening_kill_time_out_of_range: "Opening kill time is outside the 0–120s range the model accepts.",
};

export function messageFor(code: string | null | undefined, fallback = "Something went wrong. Try again."): string {
  if (!code) return fallback;
  return MESSAGES[code] ?? fallback;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(messageFor(code));
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}
