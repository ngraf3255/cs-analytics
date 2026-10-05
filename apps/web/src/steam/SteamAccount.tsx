import { FormEvent, useState } from "react";
import { steamApi, steamLoginUrl } from "./api";
import { ApiError } from "./errors";
import type { Me } from "./types";

const AUTH_CODE_URL = "https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128";
const SHARE_CODE_GUIDE_URL = "https://leetify.com/blog/share-codes/";

type Props = {
  me: Me | null;
  onChange: () => Promise<unknown>;
  onSignedOut: () => void;
};

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}

export function SteamAccount({ me, onChange, onSignedOut }: Props) {
  if (!me) {
    return (
      <div className="steam-card">
        <span className="section-kicker">STEP 1 · VERIFY YOUR STEAM ACCOUNT</span>
        <h3>Connect Steam</h3>
        <p>
          You sign in on Steam’s own site. We only learn your public SteamID. We never see or ask for your
          Steam password or Steam Guard codes.
        </p>
        <a className="steam-button" href={steamLoginUrl("/#matches")}>
          Sign in through Steam ↗
        </a>
      </div>
    );
  }
  return <SignedIn me={me} onChange={onChange} onSignedOut={onSignedOut} />;
}

function SignedIn({ me, onChange, onSignedOut }: { me: Me; onChange: () => Promise<unknown>; onSignedOut: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(errorText(reason, fallback));
    } finally {
      setBusy(false);
    }
  }

  const disconnect = () => {
    if (!window.confirm("Remove your stored Game Authentication Code and share-code cursor? Imported matches stay until you delete your data.")) return;
    void run(async () => {
      await steamApi.deleteMatchAccess();
      await onChange();
    }, "Could not disconnect match history.");
  };

  const deleteAll = () => {
    if (!window.confirm("Delete your account, stored codes and every imported match? This cannot be undone.")) return;
    void run(async () => {
      await steamApi.deleteMe();
      onSignedOut();
    }, "Could not delete your data.");
  };

  const logout = () =>
    void run(async () => {
      await steamApi.logout();
      onSignedOut();
    }, "Could not sign out.");

  return (
    <div className="steam-card">
      <span className="section-kicker">VERIFIED STEAM ACCOUNT</span>
      <h3>
        SteamID{" "}
        <a href={`https://steamcommunity.com/profiles/${me.steam_id}`} target="_blank" rel="noreferrer">
          {me.steam_id} ↗
        </a>
      </h3>
      {me.match_access.linked ? (
        <div className="steam-linked">
          <p>
            Match history linked. Game Authentication Code <code>{me.match_access.auth_code_hint}</code>
            {me.match_access.updated_at && <> · updated {new Date(me.match_access.updated_at).toLocaleString()}</>}
          </p>
          <details>
            <summary>Update codes</summary>
            <LinkForm onLinked={onChange} />
          </details>
          <p className="steam-muted">
            To revoke access on Valve’s side too, open the{" "}
            <a href={AUTH_CODE_URL} target="_blank" rel="noreferrer">Game Authentication Code page ↗</a> and create a new code.
          </p>
        </div>
      ) : (
        <LinkForm onLinked={onChange} />
      )}
      <div className="steam-actions">
        {me.match_access.linked && (
          <button type="button" className="ghost-button" onClick={disconnect} disabled={busy}>Disconnect match history</button>
        )}
        <button type="button" className="ghost-button" onClick={logout} disabled={busy}>Sign out</button>
        <button type="button" className="danger-button" onClick={deleteAll} disabled={busy}>Delete my data</button>
      </div>
      {error && <div className="steam-error" role="alert">{error}</div>}
    </div>
  );
}

function LinkForm({ onLinked }: { onLinked: () => Promise<unknown> }) {
  const [authCode, setAuthCode] = useState("");
  const [shareCode, setShareCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await steamApi.putMatchAccess({ auth_code: authCode.trim(), share_code: shareCode.trim(), consent });
      setAuthCode("");
      setShareCode("");
      setConsent(false);
      await onLinked();
    } catch (reason) {
      setError(errorText(reason, "Could not link your match history."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="steam-form" onSubmit={submit}>
      <span className="section-kicker">STEP 2 · AUTHORIZE MATCH HISTORY</span>
      <ol className="steam-steps">
        <li>
          Open Valve’s <a href={AUTH_CODE_URL} target="_blank" rel="noreferrer">Access to Your Match History ↗</a> page
          and create a <strong>Game Authentication Code</strong> (looks like <code>ABCD-EFGHI-JKLM</code>). It only allows reading your match history. It is not your password, and you can revoke it there at any time.
        </li>
        <li>
          On the same page, or in CS2 under <em>Watch → Your Matches</em>, copy the <strong>match sharing code</strong> from a recent
          Competitive, Premier or Wingman match (<code>CSGO-xxxxx-…</code>). <a href={SHARE_CODE_GUIDE_URL} target="_blank" rel="noreferrer">How share codes work ↗</a>
        </li>
        <li>
          We import matches <em>newer</em> than that code. Share codes expire after about 30 days, so this does not import
          your whole history.
        </li>
      </ol>
      <label className="field">
        <span className="field-label">GAME AUTHENTICATION CODE</span>
        <span className="number-wrap">
          <input type="password" autoComplete="off" spellCheck={false} placeholder="ABCD-EFGHI-JKLM" value={authCode}
            onChange={(event) => setAuthCode(event.target.value)} required />
        </span>
      </label>
      <label className="field">
        <span className="field-label">RECENT MATCH SHARING CODE</span>
        <span className="number-wrap">
          <input type="text" autoComplete="off" spellCheck={false} placeholder="CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx" value={shareCode}
            onChange={(event) => setShareCode(event.target.value)} required />
        </span>
      </label>
      <label className="steam-consent">
        <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
        <span>
          I allow CS Gooner to store this code encrypted and use it only to fetch my CS2 match history when I click Sync.
          I can disconnect or delete my data at any time.
        </span>
      </label>
      <button className="submit-button" type="submit" disabled={busy || !consent}>
        <span>{busy ? "CHECKING WITH VALVE" : "LINK MATCH HISTORY"}</span><span className="button-arrow">↗</span>
      </button>
      {error && <div className="steam-error" role="alert">{error}</div>}
    </form>
  );
}
