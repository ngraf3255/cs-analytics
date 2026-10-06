import { useEffect } from "react";
import { useHashScroll, useMe, useSteamStatus } from "./hooks";
import { Matches } from "./Matches";
import { SteamAccount } from "./SteamAccount";

function loginFailureReason(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get("steam_login") === "failed" ? params.get("reason") || "unknown" : null;
}

export function SteamSection() {
  const { enabled, reachable, loading } = useSteamStatus();
  const { me, setMe, error, refresh } = useMe(enabled);
  const failure = loginFailureReason();

  useEffect(() => {
    if (failure) window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  }, [failure]);

  useHashScroll(!loading);

  if (loading) return null;

  return (
    <section id="matches" className="steam-section" aria-label="Your CS2 matches">
      <div className="info-intro">
        <span className="section-kicker">YOUR MATCHES</span>
        <h2>Your rounds.<br /><em>Model in hindsight.</em></h2>
      </div>
      {!enabled && <ComingSoon reachable={reachable} />}
      {failure && <div className="steam-error" role="alert">Steam sign-in could not be verified. Please try again.</div>}
      {error && <div className="steam-error" role="alert">{error}</div>}
      {enabled && <SteamAccount me={me} onChange={refresh} onSignedOut={() => setMe(null)} />}
      {/* keyed by account: a different user signed in (e.g. in another tab) gets a fresh list */}
      {me && <Matches key={me.steam_id} me={me} onMeChange={refresh} />}
    </section>
  );
}

/** Match features aren't switched on for this deployment (or the API can't be reached):
 * say so instead of hiding the section, and point back at what works now. */
function ComingSoon({ reachable }: { reachable: boolean }) {
  return (
    <div className="steam-card empty-state" role="status">
      <span className="section-kicker">{reachable ? "COMING SOON" : "MATCH SERVICE OFFLINE"}</span>
      <h3>{reachable ? "Match reports are on the way" : "Can’t reach the match service"}</h3>
      <p>
        {reachable
          ? "Soon you’ll be able to connect Steam or upload a CS2 demo and see how the model rated every round of your matches. Until then, the round predictor above works right now."
          : "The match-report server didn’t answer. It may be waking up or restarting; refresh in a minute."}
      </p>
      <a className="ghost-button empty-state-cta" href="#predictor">TRY THE ROUND PREDICTOR ↑</a>
    </div>
  );
}
