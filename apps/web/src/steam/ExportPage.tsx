import { useEffect, useState } from "react";
import { ACCOUNT_PATH, ACCOUNT_EXPORT_PATH } from "./routes";
import { TableauExport } from "./TableauExport";
import { useMe, useSteamStatus } from "./hooks";
import { steamLoginUrl } from "./api";

/** ``/account/export``: Tableau CSV downloads — kept off the signed-in home page. */
export function ExportPage() {
  const { steam, upload, enabled, reachable, loading } = useSteamStatus();
  const { me, checked } = useMe(enabled);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!loading && (!enabled || checked)) setReady(true);
  }, [loading, enabled, checked]);

  const pending = !ready;

  return (
    <section id="export" className="steam-section account-page" aria-label="Export for Tableau" aria-busy={pending || undefined}>
      <div className="info-intro">
        <a className="account-back" href={ACCOUNT_PATH}>← Account</a>
        <h2>Export</h2>
      </div>
      {pending ? (
        <div className="steam-card account-loading" aria-label="Loading export">
          <span /><span /><span />
        </div>
      ) : !enabled ? (
        <div className="steam-card" role="status">
          <p>{reachable ? "Exports aren’t switched on yet." : "Can’t reach the match service. Refresh in a minute."}</p>
        </div>
      ) : !me ? (
        <div className="steam-card">
          <p>Sign in to download your rounds and matches as CSV.</p>
          {steam || upload ? (
            <a className="steam-button" href={steamLoginUrl(ACCOUNT_EXPORT_PATH)}>Sign in through Steam ↗</a>
          ) : null}
        </div>
      ) : (
        <div className="steam-card export-page-card">
          <span className="section-kicker">TABLEAU</span>
          <p className="steam-muted">Rounds and matches CSV for your imported demos.</p>
          <TableauExport layout="page" />
        </div>
      )}
    </section>
  );
}
