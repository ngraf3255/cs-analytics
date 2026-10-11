import { useEffect, useState } from "react";
import { useMe, useSteamStatus } from "./hooks";
import { hasSessionHint, setSessionHint } from "./sessionHint";

/** Give up waiting on auth after this long and treat the visitor as a guest. */
export const HERO_AUTH_TIMEOUT_MS = 4000;

/** Short marketing hero for signed-out visitors on `/`.
 * Decided on first paint: no session hint → render now; hint → wait for auth (max 4 s). */
export function GuestHero() {
  const [hint] = useState(hasSessionHint);
  const { enabled, loading } = useSteamStatus();
  const { me, checked, error } = useMe(enabled);
  const [timedOut, setTimedOut] = useState(false);
  useEffect(() => {
    if (!hint) return;
    const t = setTimeout(() => setTimedOut(true), HERO_AUTH_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [hint]);
  const settled = !loading && (!enabled || checked);
  const guest = settled && !me;
  // A real "signed out" answer clears the stale hint; a /me error or timeout shows the hero but keeps it.
  useEffect(() => { if (hint && guest && !error) setSessionHint(false); }, [hint, guest, error]);

  const show = hint ? !me && (settled || timedOut) : !me;
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
