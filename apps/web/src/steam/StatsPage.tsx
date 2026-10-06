import { useEffect, useState } from "react";
import { steamLoginUrl } from "./api";
import { useMe, useSteamStatus } from "./hooks";
import { MatchesSummaryPanel } from "./MatchesSummary";
import { ACCOUNT_PATH, STATS_PATH } from "./routes";

/** ``/stats``: lobby compare, roles, map tables, all-players — off the home landing. */
export function StatsPage() {
  const { steam, upload, enabled, reachable, loading } = useSteamStatus();
  const { me, checked } = useMe(enabled);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!loading && (!enabled || checked)) setReady(true);
  }, [loading, enabled, checked]);

  const pending = !ready;

  return (
    <section id="stats" className="steam-section account-page" aria-label="Your stats" aria-busy={pending || undefined}>
      <div className="info-intro">
        <a className="account-back" href="/">← Home</a>
        <h2>Stats</h2>
      </div>
      {pending ? (
        <div className="steam-card account-loading" aria-label="Loading stats">
          <span /><span /><span />
        </div>
      ) : !enabled ? (
        <div className="steam-card" role="status">
          <p>{reachable ? "Stats aren’t switched on yet." : "Can’t reach the match service. Refresh in a minute."}</p>
        </div>
      ) : !me ? (
        <div className="steam-card">
          <p>Sign in to see lobby compare, roles, and map tables.</p>
          {(steam || upload) && <a className="steam-button" href={steamLoginUrl(STATS_PATH)}>Sign in through Steam ↗</a>}
          <p className="steam-muted"><a href={ACCOUNT_PATH}>Account settings</a></p>
        </div>
      ) : (
        <div className="steam-card">
          <MatchesSummaryPanel variant="stats" refreshKey={1} />
        </div>
      )}
    </section>
  );
}
