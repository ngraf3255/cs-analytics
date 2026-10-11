import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./errors";
import { steamApi } from "./api";
import { sessionEpoch, setSessionHint } from "./sessionHint";
import type { Me } from "./types";

export type Features = {
  /** Steam sign-in, match-history linking and sync. */
  steam: boolean;
  /** Demo upload + match reports for a signed-in account. */
  upload: boolean;
  /** Upload without Steam (guest session). */
  guest: boolean;
  /** GET /steam/status answered (false: API down / waking up). */
  reachable: boolean;
};

const NO_FEATURES: Features = { steam: false, upload: false, guest: false, reachable: false };

export function useSteamStatus() {
  const [features, setFeatures] = useState<Features>(NO_FEATURES);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    steamApi
      .status()
      .then((body) => {
        if (cancelled) return;
        const steam = Boolean(body.steam ?? body.enabled);
        // Older APIs only send ``enabled`` (uploads were part of Steam then).
        setFeatures({ steam, upload: Boolean(body.upload ?? steam), guest: Boolean(body.guest), reachable: true });
      })
      .catch(() => {
        if (!cancelled) setFeatures(NO_FEATURES);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { ...features, enabled: features.steam || features.upload, loading };
}

export function useMe(enabled: boolean) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // Which ``enabled`` value the last /me answer was for: until it matches, pages
  // shouldn't render a signed-out state (avoids a "Sign in" flash while /me loads).
  const [checkedFor, setCheckedFor] = useState<boolean | null>(null);

  // Background re-checks (focus / hashchange) must not flip ``loading``: SteamSection unmounts
  // while loading, which remounted the match list and flashed its skeleton rows.
  const answered = useRef(false);
  const refresh = useCallback(async () => {
    if (!enabled) {
      setMe(null);
      setCheckedFor(false);
      return null;
    }
    if (!answered.current) setLoading(true);
    setError("");
    const started = sessionEpoch();
    try {
      const body = await steamApi.me();
      if (sessionEpoch() !== started) return null;  // began before a sign-out: ignore
      setMe((prev) => (JSON.stringify(prev) === JSON.stringify(body) ? prev : body));
      setSessionHint(true);
      return body;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        setSessionHint(false);
        setMe(null);
        return null;
      }
      setError(reason instanceof ApiError ? reason.message : "Could not load your Steam account.");
      return null;
    } finally {
      answered.current = true;
      setLoading(false);
      setCheckedFor(true);
    }
  }, [enabled]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The session cookie can change underneath an open tab (sign-in / sign-out in another tab,
  // or /dev/login locally, which only lands on /#matches): re-check who is signed in when the
  // tab regains focus or the hash changes. One request at a time (focus + visibilitychange
  // usually fire together).
  const inFlight = useRef(false);
  useEffect(() => {
    if (!enabled) return;
    const recheck = () => {
      if (inFlight.current) return;
      inFlight.current = true;
      void refresh().finally(() => { inFlight.current = false; });
    };
    const onVisibility = () => { if (document.visibilityState === "visible") recheck(); };
    window.addEventListener("focus", recheck);
    window.addEventListener("hashchange", recheck);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", recheck);
      window.removeEventListener("hashchange", recheck);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, refresh]);

  return { me, setMe, loading, error, refresh, checked: checkedFor === enabled };
}

/** Scroll to the element named by the URL hash (e.g. #matches) once ``ready`` and on every
 * hashchange. The browser's own jump happens before this section exists (it renders after
 * GET /steam/status), so a fresh load of /#matches would otherwise stay at the top. */
/** How long the anchor is re-pinned while content above it settles (auth, tiles, list). */
export const HASH_PIN_MS = 5000;

export function useHashScroll(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const target = () => document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    const scroll = () => target()?.scrollIntoView?.({ block: "start" });
    scroll();
    // Re-pin while layout above the anchor settles; stop on the first user scroll intent or after HASH_PIN_MS.
    let pinning = Boolean(target());
    const stop = () => { pinning = false; };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => { if (pinning) scroll(); });
    if (pinning) observer?.observe(document.body);
    const intents = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
    intents.forEach((e) => window.addEventListener(e, stop, { passive: true, once: true }));
    const timer = window.setTimeout(stop, HASH_PIN_MS);
    window.addEventListener("hashchange", scroll);
    return () => {
      observer?.disconnect();
      window.clearTimeout(timer);
      intents.forEach((e) => window.removeEventListener(e, stop));
      window.removeEventListener("hashchange", scroll);
    };
  }, [ready]);
}
