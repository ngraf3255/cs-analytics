import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./errors";
import { steamApi } from "./api";
import type { Me } from "./types";

export function useSteamStatus() {
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    steamApi
      .status()
      .then((body) => {
        if (!cancelled) setEnabled(Boolean(body.enabled));
      })
      .catch(() => {
        if (!cancelled) setEnabled(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { enabled, loading };
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

  return { me, setMe, loading, error, refresh };
}
