import { useEffect, useState } from "react";
import { steamLoginUrl } from "./api";
import { useMe, useSteamStatus } from "./hooks";
import { ACCOUNT_PATH } from "./routes";
import { AccountSettings, SteamAccount } from "./SteamAccount";

/** ``/account``: everything about linking and the account, kept off the main matches page. */
export function AccountPage() {
  const { steam, upload, guest, enabled, reachable, loading } = useSteamStatus();
  const { me, setMe, error, refresh, checked } = useMe(enabled);
  // read once: the flag is stripped from the URL below and must survive re-renders
  const [failure] = useState(() => new URLSearchParams(window.location.search).get("steam_login") === "failed");

  useEffect(() => {
    if (failure) window.history.replaceState(null, "", window.location.pathname);
  }, [failure]);

  const pending = loading || (enabled && !checked);

  return (
    <section id="account" className="steam-section account-page" aria-label="Account settings" aria-busy={pending || undefined}>
      <div className="info-intro">
        <a className="account-back" href="/#matches">← Your rounds</a>
        <h2>Account settings</h2>
      </div>
      {pending ? (
        // Status + session still loading (the API can take a moment to wake): keep the page's shape
        // instead of a blank screen.
        <div className="steam-card account-loading" aria-label="Loading your account">
          <span /><span /><span />
        </div>
      ) : !enabled ? (
        <div className="steam-card" role="status">
          <p>{reachable ? "Accounts aren’t switched on yet." : "Can’t reach the match service. Refresh in a minute."}</p>
        </div>
      ) : (
        <>
          {failure && <div className="steam-error" role="alert">Steam sign-in could not be verified. Please try again.</div>}
          {error && <div className="steam-error" role="alert">{error}</div>}
          {me && me.account !== "guest" ? (
            <AccountSettings me={me} onChange={refresh} onSignedOut={() => { setMe(null); window.location.assign("/#matches"); }} />
          ) : me ? (
            <SteamAccount me={me} steam={steam} guest={guest && upload} onChange={refresh}
              onSignedOut={() => { setMe(null); window.location.assign("/#matches"); }} />
          ) : steam ? (
            <div className="steam-card">
              <p>Sign in to manage your account.</p>
              <a className="steam-button" href={steamLoginUrl(ACCOUNT_PATH)}>Sign in through Steam ↗</a>
            </div>
          ) : (
            <div className="steam-card" role="status"><p>Steam sign-in is coming soon.</p></div>
          )}
        </>
      )}
    </section>
  );
}
