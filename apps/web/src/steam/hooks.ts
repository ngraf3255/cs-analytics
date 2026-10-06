import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./errors";
import { steamApi } from "./api";
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

  const refresh = useCallback(async () => {
    if (!enabled) {
      setMe(null);
      return null;
    }
    setLoading(true);
    setError("");
    try {
      const body = await steamApi.me();
      setMe(body);
      return body;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        setMe(null);
        return null;
      }
      setError(reason instanceof ApiError ? reason.message : "Could not load your Steam account.");
      return null;
    } finally {
      setLoading(false);
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

  return { me, setMe, loading, error, refresh };
}

/** Scroll to the element named by the URL hash (e.g. #matches) once ``ready`` and on every
 * hashchange. The browser's own jump happens before this section exists (it renders after
 * GET /steam/status), so a fresh load of /#matches would otherwise stay at the top. */
export function useHashScroll(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const scroll = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (id) document.getElementById(id)?.scrollIntoView?.({ block: "start" });
    };
    scroll();
    window.addEventListener("hashchange", scroll);
    return () => window.removeEventListener("hashchange", scroll);
  }, [ready]);
}
