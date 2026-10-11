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

/** Bumped on sign-out / account delete; /me answers that started before it are ignored. */
let epoch = 0;
export const sessionEpoch = () => epoch;

/** Sign-out / delete: drop the hint synchronously (before any request or navigation). */
export function endSession(): void {
  epoch += 1;
  setSessionHint(false);
}

/** ``auth=1`` on the page Steam sign-in returns to: we know a session exists before /me answers. */
const MARKER = "auth";

/** Add ``?auth=1`` to a same-site path, before any ``#hash``. */
export function withAuthMarker(path: string): string {
  const [beforeHash, hash] = path.split("#", 2);
  const sep = beforeHash.includes("?") ? "&" : "?";
  return `${beforeHash}${sep}${MARKER}=1${hash !== undefined ? `#${hash}` : ""}`;
}

/** Before the first render: ``auth=1`` → set the session hint, then drop the marker (hash kept). */
export function consumeAuthMarker(): void {
  const url = new URL(window.location.href);
  if (url.searchParams.get(MARKER) !== "1") return;
  setSessionHint(true);
  url.searchParams.delete(MARKER);
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
}
