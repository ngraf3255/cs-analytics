import { FormEvent, useEffect, useRef, useState } from "react";
import { steamApi, steamLoginUrl } from "./api";
import { ApiError, messageFor } from "./errors";
import { everyText, timeAgo, timeUntil } from "./format";
import {
  AUTH_CODE_EXAMPLE, AUTH_CODE_URL, SHARE_CODE_EXAMPLE, SHARE_CODE_GUIDE_URL, VALVE_MATCH_HISTORY_DOCS_URL,
  checkAuthCode, checkShareCode, errorField,
} from "./linkInput";
import type { AutoSync, Me, NeedsRelink } from "./types";

type Props = {
  me: Me | null;
  /** Steam sign-in is configured on the server (else "Connect Steam" is shown as coming soon). */
  steam?: boolean;
  /** Uploading without Steam (guest session) is available. */
  guest?: boolean;
  onChange: () => Promise<unknown>;
  onSignedOut: () => void;
  /** A guest session was started (with the demo the user picked, to upload right away). */
  onGuest?: (me: Me, file?: File) => void;
};

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}

/** Why the last sync needs new codes (``me.match_access.needs_relink``). */
export const RELINK_TEXT: Record<string, string> = {
  invalid_known_code:
    "Your saved share code stopped working. Valve only accepts one from the last 30 days, so after a break from matchmaking it needs a fresh one. Paste the share code of your most recent match below. Your authentication code is kept.",
  invalid_auth_code:
    "Valve rejected your saved Game Authentication Code. It was probably revoked or replaced on Valve’s page. Paste your current code below.",
  credentials_unreadable:
    "Your saved Game Authentication Code can’t be read any more (the server’s key changed). Paste it again below.",
};

export function SteamAccount({ me, steam = true, guest = false, onChange, onSignedOut, onGuest }: Props) {
  if (!me) {
    return (
      <div className={`account-options ${guest ? "two" : ""}`}>
        {steam ? (
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
        ) : (
          <div className="steam-card soft-disabled" aria-label="Connect Steam (coming soon)">
            <span className="section-kicker">STEAM SYNC · COMING SOON</span>
            <h3>Connect Steam</h3>
            <p>
              Automatic match import from your Steam match history isn’t switched on yet.
              {guest ? " You can already upload demos and get full round reports." : ""}
            </p>
            <button type="button" className="steam-button" disabled aria-disabled="true">Sign in through Steam · soon</button>
          </div>
        )}
        {guest && onGuest && <GuestStart onGuest={onGuest} />}
      </div>
    );
  }
  if (me.account === "guest") return <GuestAccount steam={steam} onSignedOut={onSignedOut} />;
  return <SignedIn me={me} onChange={onChange} onSignedOut={onSignedOut} />;
}

const DEMO_ACCEPT = ".dem,.bz2,application/x-bzip2";

/** Signed out: upload a demo right away, no Steam account needed (starts a guest session). */
function GuestStart({ onGuest }: { onGuest: (me: Me, file?: File) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function start(file?: File) {
    setBusy(true);
    setError("");
    try {
      onGuest(await steamApi.startGuest(), file);
    } catch (reason) {
      setError(errorText(reason, "Could not start a guest session."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="steam-card">
      <span className="section-kicker">NO STEAM NEEDED</span>
      <h3>Upload a demo</h3>
      <p>
        Drop in any CS2 <code>.dem</code> or <code>.dem.bz2</code> (your replays live in <code>game/csgo/replays</code>)
        and get a round-by-round report. Your uploads stay tied to this browser.
      </p>
      <div className="steam-actions">
        <label className={`steam-button upload-button guest-upload ${busy ? "busy" : ""}`} aria-disabled={busy}>
          <span>{busy ? "STARTING…" : "CHOOSE A DEMO FILE"}</span>
          <input type="file" accept={DEMO_ACCEPT} disabled={busy} aria-label="Choose a demo file to upload without Steam"
            onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void start(file); }} />
        </label>
        <button type="button" className="ghost-button" onClick={() => void start()} disabled={busy}>Open my uploads</button>
      </div>
      {error && <div className="steam-error" role="alert">{error}</div>}
    </div>
  );
}

/** Signed in as a guest (POST /auth/guest). */
function GuestAccount({ steam, onSignedOut }: { steam: boolean; onSignedOut: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
      onSignedOut();
    } catch (reason) {
      setError(errorText(reason, fallback));
    } finally {
      setBusy(false);
    }
  }

  const signOut = () => {
    if (!window.confirm("Sign out of this guest session? Guest uploads can’t be opened again after signing out.")) return;
    void run(() => steamApi.logout(), "Could not sign out.");
  };
  const deleteAll = () => {
    if (!window.confirm("Delete this guest session and every match you uploaded? This cannot be undone.")) return;
    void run(() => steamApi.deleteMe(), "Could not delete your data.");
  };

  return (
    <div className="steam-card">
      <span className="section-kicker">GUEST SESSION</span>
      <h3>Uploading as a guest</h3>
      <p>
        Your uploads and reports are tied to this browser for about two weeks. Clearing cookies or signing out ends access to them.
        {steam ? " Sign in through Steam for a permanent list with automatic match sync (it starts a separate list)." : " Steam sign-in and automatic sync are coming soon."}
      </p>
      <div className="steam-actions">
        {steam && <a className="steam-button" href={steamLoginUrl("/#matches")}>Sign in through Steam ↗</a>}
        <button type="button" className="ghost-button" onClick={signOut} disabled={busy}>Sign out</button>
        <button type="button" className="danger-button" onClick={deleteAll} disabled={busy}>Delete my data</button>
      </div>
      {error && <div className="steam-error" role="alert">{error}</div>}
    </div>
  );
}

function SignedIn({ me, onChange, onSignedOut }: { me: Me; onChange: () => Promise<unknown>; onSignedOut: () => void }) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const access = me.match_access;
  const relink = access.linked ? access.needs_relink ?? null : null;

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError("");
    setNotice("");
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
      setNotice("Match history disconnected. Your imported matches stay. Link again below any time.");
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

  const linked = async () => {
    setNotice("");
    await onChange();
  };

  return (
    <div className="steam-card">
      <span className="section-kicker">VERIFIED STEAM ACCOUNT</span>
      <h3>
        SteamID{" "}
        <a href={`https://steamcommunity.com/profiles/${me.steam_id}`} target="_blank" rel="noreferrer">
          {me.steam_id} ↗
        </a>
      </h3>
      {notice && <div className="steam-notice" role="status">{notice}</div>}
      {access.linked ? (
        <div className="steam-linked">
          <p>
            Match history linked. Game Authentication Code <code>{access.auth_code_hint}</code>
            {access.updated_at && <> · updated {new Date(access.updated_at).toLocaleString()}</>}
          </p>
          <AutoSyncStatus me={me} onChange={onChange} />
          {relink ? (
            <>
              <div className="steam-error relink-alert" role="alert">
                <strong>Re-link needed to keep syncing.</strong> {RELINK_TEXT[relink.reason] ?? RELINK_TEXT.invalid_known_code}
              </div>
              <LinkForm onLinked={linked} linked authHint={access.auth_code_hint} relink={relink} />
            </>
          ) : (
            <details>
              <summary>Update codes (e.g. a new share code after a break)</summary>
              <LinkForm onLinked={linked} linked authHint={access.auth_code_hint} />
            </details>
          )}
          <p className="steam-muted">
            To revoke access on Valve’s side too, open the{" "}
            <a href={AUTH_CODE_URL} target="_blank" rel="noreferrer">Game Authentication Code page ↗</a> and create a new code.
          </p>
        </div>
      ) : (
        <LinkForm onLinked={linked} linked={false} />
      )}
      <div className="steam-actions">
        {access.linked && (
          <button type="button" className="ghost-button" onClick={disconnect} disabled={busy}>Disconnect match history</button>
        )}
        <button type="button" className="ghost-button" onClick={logout} disabled={busy}>Sign out</button>
        <button type="button" className="danger-button" onClick={deleteAll} disabled={busy}>Delete my data</button>
      </div>
      {error && <div className="steam-error" role="alert">{error}</div>}
    </div>
  );
}

/** Why automatic sync isn't running for this user (``auto_sync.paused_reason``). */
export const AUTO_SYNC_PAUSED_TEXT: Record<string, string> = {
  turned_off: "auto-sync off",
  needs_relink: "auto-sync paused until you re-link",
  not_linked: "auto-sync starts once you link",
  server_disabled: "auto-sync isn’t available on this server",
  demo_retrieval_not_configured: "auto-sync starts once the server can download demos",
};

/** "Last synced 5 min ago · auto-sync on" plus the on/off toggle (linked accounts). */
export function AutoSyncStatus({ me, onChange }: { me: Me; onChange: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [override, setOverride] = useState<AutoSync | null>(null);
  const [pending, setPending] = useState<boolean | null>(null);  // the switch flips at once
  const auto = override ?? me.sync.auto_sync;
  useEffect(() => setOverride(null), [me]);
  if (!auto) return null;  // older API without automatic sync
  const lastSynced = me.sync.last_synced_at ?? null;
  const serverOff = auto.paused_reason === "server_disabled";

  async function toggle(enabled: boolean) {
    setBusy(true);
    setError("");
    setPending(enabled);
    try {
      setOverride(await steamApi.putAutoSync(enabled));
      await onChange();
    } catch (reason) {
      setError(errorText(reason, "Could not change automatic sync."));
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  const state = auto.active ? "auto-sync on" : AUTO_SYNC_PAUSED_TEXT[auto.paused_reason ?? ""] ?? "auto-sync paused";
  return (
    <div className="auto-sync" aria-label="Automatic sync">
      <p className="auto-sync-status" role="status">
        {lastSynced ? `Last synced ${timeAgo(lastSynced)}` : "Not synced yet"}, {state}
        {auto.active && auto.next_at && <span className="steam-muted"> · next check {timeUntil(auto.next_at)}</span>}
      </p>
      {auto.active && auto.last_error && (
        <p className="steam-muted auto-sync-error">
          Last automatic sync didn’t work: {messageFor(auto.last_error, "unexpected error.")}
          {auto.failures > 1 && ` (${auto.failures} times in a row; retrying less often)`}
        </p>
      )}
      {!serverOff && (
        <label className="steam-consent auto-sync-toggle">
          <input type="checkbox" role="switch" checked={pending ?? auto.enabled} disabled={busy}
            onChange={(event) => void toggle(event.target.checked)} />
          <span>
            Sync new matches automatically{auto.interval_seconds ? ` (checks Valve ${everyText(auto.interval_seconds)})` : ""}.
            Off: matches only come in when you click Sync.
          </span>
        </label>
      )}
      {error && <div className="steam-error" role="alert">{error}</div>}
    </div>
  );
}

type LinkFormProps = {
  onLinked: () => Promise<unknown>;
  /** Already linked: an empty auth-code box keeps the stored code (only the share code changes). */
  linked: boolean;
  authHint?: string | null;
  /** The last sync needs new codes: which one to replace. */
  relink?: NeedsRelink | null;
};

function LinkForm({ onLinked, linked, authHint, relink }: LinkFormProps) {
  const [authCode, setAuthCode] = useState("");
  const [shareCode, setShareCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<{ field: "auth" | "share"; text: string } | null>(null);
  const [touched, setTouched] = useState({ auth: false, share: false });
  const authRef = useRef<HTMLInputElement>(null);
  const shareRef = useRef<HTMLInputElement>(null);

  // The stored auth code can be kept unless it is the one that stopped working.
  const authOptional = linked && relink?.field !== "auth_code";
  const auth = checkAuthCode(authCode, authOptional);
  const share = checkShareCode(shareCode);
  const ids = { auth: "link-auth-hint", share: "link-share-hint" };

  useEffect(() => {
    if (relink) (relink.field === "auth_code" ? authRef : shareRef).current?.focus();
  }, [relink]);

  const authMessage = fieldError?.field === "auth" ? fieldError.text : touched.auth && authCode.trim() ? auth.hint : null;
  const shareMessage = fieldError?.field === "share" ? fieldError.text : touched.share && shareCode.trim() ? share.hint : null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched({ auth: true, share: true });
    setError("");
    setFieldError(null);
    if (!auth.ok || !share.ok) {
      if (!auth.ok) {
        if (!authCode.trim()) setFieldError({ field: "auth", text: `Paste your Game Authentication Code (${AUTH_CODE_EXAMPLE}).` });
        authRef.current?.focus();
      } else {
        if (!shareCode.trim()) setFieldError({ field: "share", text: "Paste a recent match sharing code (CSGO-…)." });
        shareRef.current?.focus();
      }
      return;
    }
    setBusy(true);
    try {
      await steamApi.putMatchAccess({ auth_code: auth.value, share_code: share.value, consent });
      setAuthCode("");
      setShareCode("");
      setConsent(false);
      setTouched({ auth: false, share: false });
      await onLinked();
    } catch (reason) {
      const field = reason instanceof ApiError ? errorField(reason.code) : null;
      if (field) {
        setFieldError({ field, text: errorText(reason, "Check this code.") });
        (field === "auth" ? authRef : shareRef).current?.focus();
      } else setError(errorText(reason, "Could not link your match history."));
    } finally {
      setBusy(false);
    }
  }

  const foundInLink = share.ok && share.value !== shareCode.trim();
  return (
    <form className="steam-form" onSubmit={submit} noValidate aria-label="Link match history">
      <span className="section-kicker">{linked ? "UPDATE MATCH-HISTORY CODES" : "STEP 2 · AUTHORIZE MATCH HISTORY"}</span>
      <ol className="steam-steps">
        <li>
          Open Valve’s <a href={AUTH_CODE_URL} target="_blank" rel="noreferrer">Access to Your Match History ↗</a> page
          (sign in to Steam Support if it asks). Copy your <strong>Game Authentication Code</strong> (<code>{AUTH_CODE_EXAMPLE}</code>);
          create one there if the page shows none. It only lets apps read your match history. It is not your password,
          and you can revoke it on the same page at any time.
        </li>
        <li>
          On the same page, copy <em>Your most recently completed match token</em>. That is a <strong>match sharing code</strong>{" "}
          (<code>CSGO-xxxxx-…</code>). Or, in CS2, open <em>Watch → Your Matches</em> and copy the sharing code of one of your
          last matches. Pasting the whole <code>steam://…</code> share link is fine too. No code yet? Play a Competitive,
          Premier or Wingman match first.
        </li>
        <li>
          We import that match and every <em>newer</em> one, not your whole history: Valve’s codes expire after about 30 days.
          For older matches, FACEIT or pro games, upload the demo file below.{" "}
          <a href={SHARE_CODE_GUIDE_URL} target="_blank" rel="noreferrer">How share codes work ↗</a>{" · "}
          <a href={VALVE_MATCH_HISTORY_DOCS_URL} target="_blank" rel="noreferrer">Valve’s docs ↗</a>
        </li>
      </ol>
      <label className="field">
        <span className="field-label">
          GAME AUTHENTICATION CODE{authOptional && <span className="unit">OPTIONAL · LEAVE EMPTY TO KEEP {authHint ?? "YOURS"}</span>}
        </span>
        <span className="number-wrap">
          <input ref={authRef} type="password" autoComplete="off" spellCheck={false} name="auth_code"
            placeholder={authOptional ? `Keep ${authHint ?? "current code"}` : AUTH_CODE_EXAMPLE} value={authCode}
            aria-invalid={authMessage ? true : undefined} aria-describedby={authMessage ? ids.auth : undefined}
            onChange={(event) => { setAuthCode(event.target.value); if (fieldError?.field === "auth") setFieldError(null); }}
            onBlur={() => setTouched((t) => ({ ...t, auth: true }))} />
        </span>
        {authMessage
          ? <span id={ids.auth} className="field-hint error">{authMessage}</span>
          : auth.ok && auth.value && <span className="field-hint ok">Looks right.</span>}
      </label>
      <label className="field">
        <span className="field-label">{relink?.field === "share_code" ? "NEW MATCH SHARING CODE" : "RECENT MATCH SHARING CODE"}</span>
        <span className="number-wrap">
          <input ref={shareRef} type="text" autoComplete="off" spellCheck={false} name="share_code" placeholder={SHARE_CODE_EXAMPLE} value={shareCode}
            aria-invalid={shareMessage ? true : undefined} aria-describedby={shareMessage ? ids.share : undefined}
            onChange={(event) => { setShareCode(event.target.value); if (fieldError?.field === "share") setFieldError(null); }}
            onBlur={() => setTouched((t) => ({ ...t, share: true }))} />
        </span>
        {shareMessage
          ? <span id={ids.share} className="field-hint error">{shareMessage}</span>
          : share.ok && <span className="field-hint ok">{foundInLink ? `Found ${share.value} in the link.` : "Looks right."}</span>}
      </label>
      <label className="steam-consent">
        <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
        <span>
          I allow CS Gooner to store this code encrypted and use it only to fetch my CS2 match history: automatically in
          the background (I can turn that off) and when I click Sync. I can disconnect or delete my data at any time.
        </span>
      </label>
      <button className="submit-button" type="submit" disabled={busy || !consent}>
        <span>{busy ? "CHECKING WITH VALVE" : linked ? "SAVE NEW CODES" : "LINK MATCH HISTORY"}</span><span className="button-arrow">↗</span>
      </button>
      {!consent && !busy && <span className="field-hint">Tick the box above to enable linking.</span>}
      {error && <div className="steam-error" role="alert">{error}</div>}
    </form>
  );
}
