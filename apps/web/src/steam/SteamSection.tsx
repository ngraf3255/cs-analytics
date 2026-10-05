import { useEffect } from "react";
import { useMe, useSteamStatus } from "./hooks";
import { SteamAccount } from "./SteamAccount";

function loginFailureReason(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get("steam_login") === "failed" ? params.get("reason") || "unknown" : null;
}

export function SteamSection() {
  const { enabled, loading } = useSteamStatus();
  const { me, setMe, error, refresh } = useMe(enabled);
  const failure = loginFailureReason();

  useEffect(() => {
    if (failure) window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  }, [failure]);

  if (loading || !enabled) return null;

  return (
    <section id="matches" className="steam-section" aria-label="Your CS2 matches">
      <div className="info-intro">
        <span className="section-kicker">YOUR MATCHES</span>
        <h2>Your rounds.<br /><em>Model in hindsight.</em></h2>
      </div>
      {failure && <div className="steam-error" role="alert">Steam sign-in could not be verified. Please try again.</div>}
      {error && <div className="steam-error" role="alert">{error}</div>}
      <SteamAccount me={me} onChange={refresh} onSignedOut={() => setMe(null)} />
    </section>
  );
}
