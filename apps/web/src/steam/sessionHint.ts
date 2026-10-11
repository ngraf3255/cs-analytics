/** Non-secret "this browser had a session" hint, read synchronously on first paint so the guest
 * hero can be decided before any network call. Never used for auth. */
const KEY = "csa_has_session";

export function hasSessionHint(): boolean {
  try { return window.localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function setSessionHint(on: boolean): void {
  try {
    if (on) window.localStorage.setItem(KEY, "1");
    else window.localStorage.removeItem(KEY);
  } catch { /* storage unavailable */ }
}
