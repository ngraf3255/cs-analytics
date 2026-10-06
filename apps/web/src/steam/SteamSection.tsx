import { useCallback, useEffect, useState } from "react";
import { useHashScroll, useMe, useSteamStatus } from "./hooks";
import { Matches } from "./Matches";
import { MatchesTeaser } from "./MatchesTeaser";
import { SteamAccount } from "./SteamAccount";

function loginFailureReason(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get("steam_login") === "failed" ? params.get("reason") || "unknown" : null;
}

export function SteamSection() {
  const { steam, upload, guest, reachable, enabled, loading } = useSteamStatus();
  const { me, setMe, error, refresh } = useMe(enabled);
  const failure = loginFailureReason();
  // A demo picked before a guest session existed: uploaded once the session is ready.
  const [pendingFile, setPendingFile] = useState<File | null>(null);

  useEffect(() => {
    if (failure) window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  }, [failure]);

  useHashScroll(!loading);

  const onGuest = useCallback((next: typeof me, file?: File) => {
    if (file) setPendingFile(file);
    setMe(next);
  }, [setMe]);

  if (loading) return null;

  const isGuest = me?.account === "guest";
  const signedInSteam = Boolean(me && !isGuest);
  return (
    <>
      <section id="matches" className="steam-section" aria-label="Your CS2 matches">
        <div className="info-intro">
          <h2>{signedInSteam ? "Matches" : "Your rounds"}</h2>
        </div>
        {!enabled ? (
          <ComingSoon reachable={reachable} />
        ) : (
          <>
            {failure && <div className="steam-error" role="alert">Steam sign-in could not be verified. Please try again.</div>}
            {error && <div className="steam-error" role="alert">{error}</div>}
            {/* Signed-in Steam users: account lives in the header avatar → /account (no SteamID card here). */}
            <SteamAccount me={me} steam={steam} guest={guest && upload} onChange={refresh}
              onSignedOut={() => { setPendingFile(null); setMe(null); }} onGuest={onGuest}
              homeChrome />
            {me && (
              <Matches key={me.steam_id ?? `guest-${me.created_at}`} me={me} onMeChange={refresh}
                canSync={steam && !isGuest} steamAvailable={steam}
                initialFile={pendingFile} onInitialFile={() => setPendingFile(null)} />
            )}
            {!me && <MatchesTeaser />}
          </>
        )}
      </section>
    </>
  );
}

function ComingSoon({ reachable }: { reachable: boolean }) {
  return (
    <div className="steam-card empty-state" role="status">
      <span className="section-kicker">{reachable ? "COMING SOON" : "MATCH SERVICE OFFLINE"}</span>
      <h3>{reachable ? "Match reports are on the way" : "Can’t reach the match service"}</h3>
      <p>
        {reachable
          ? "Soon you’ll be able to connect Steam or upload a CS2 demo and see how the model rated every round of your matches."
          : "The match-report server didn’t answer. It may be waking up or restarting; refresh in a minute."}
      </p>
      <a className="ghost-button empty-state-cta" href="/#predictor">TRY THE ROUND PREDICTOR →</a>
    </div>
  );
}
