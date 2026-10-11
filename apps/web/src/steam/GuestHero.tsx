import { useEffect, useState } from "react";
import { useMe, useSteamStatus } from "./hooks";
import { hasSessionHint, setSessionHint } from "./sessionHint";

/** Give up waiting on auth after this long and treat the visitor as a guest. */
export const HERO_AUTH_TIMEOUT_MS = 4000;

/** Short marketing hero for signed-out visitors on `/`.
 * Decided on first paint: no session hint → render now; hint → wait for auth (max 4 s). */
/** Guest chrome (hero, Connect Steam) visibility, decided on first paint:
 * no session hint → show unless signed in; hint → wait for auth (max 4 s), show only if it ends signed-out. */
export function useGuestChrome(me: unknown, checked: boolean, error: string, enabled: boolean, loading: boolean): boolean {
  const [hint] = useState(hasSessionHint);
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (!hint) return;
    const t = setTimeout(() => setTimedOut(true), HERO_AUTH_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [hint]);
  const settled = !loading && (!enabled || checked);
  const guest = settled && !me;
  // A real "signed out" answer clears the stale hint; a /me error or timeout shows guest chrome but keeps it.
  useEffect(() => { if (hint && guest && !error) setSessionHint(false); }, [hint, guest, error]);
  return hint ? !me && (settled || timedOut) : !me;
}

export function GuestHero() {
  const { enabled, loading } = useSteamStatus();
  const { me, checked, error } = useMe(enabled);
  const show = useGuestChrome(me, checked, error, enabled, loading);
  if (!show) return null;
  return (
    <section className="hero">
      <div className="hero-copy">
        <h1>Every round<br />has a <em>turning point.</em></h1>
      </div>
      <div className="hero-grid" aria-hidden="true" />
    </section>
  );
}
