import { FormEvent, useEffect, useRef, useState } from "react";
import { steamApi, steamLoginUrl } from "./api";
import { ApiError, messageFor } from "./errors";
import { timeAgo, timeUntil } from "./format";
import {
  AUTH_CODE_EXAMPLE, AUTH_CODE_URL, SHARE_CODE_EXAMPLE,
  checkAuthCode, checkShareCode, errorField,
} from "./linkInput";
import { ACCOUNT_PATH } from "./routes";
import type { AutoSync, Me } from "./types";

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
  invalid_known_code: "Paste a share code from a match in the last 30 days.",
  invalid_auth_code: "Paste your current Game Authentication Code.",
  credentials_unreadable: "Paste your Game Authentication Code again.",
};

export function SteamAccount({ me, steam = true, guest = false, onSignedOut, onGuest }: Props) {
  if (!me) {
    return (
      <div className={`account-options ${guest ? "two" : ""}`}>
        {steam ? (
          <div className="steam-card">
            <span className="section-kicker">STEAM</span>
            <h3>Connect Steam</h3>
            <p>Sign in to score your matches.</p>
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
  return <SignedInSummary me={me} />;
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

/** Signed in, main matches page: who you are plus a link to Account settings. No forms here;
 * when sync is blocked (re-link / first share code) it says so and links to the form. */
function SignedInSummary({ me }: { me: Me }) {
  const access = me.match_access;
  const relink = access.linked ? access.needs_relink ?? null : null;
  return (
    <div className="steam-card account-summary">
      <p>
        Signed in as{" "}
        <a href={`https://steamcommunity.com/profiles/${me.steam_id}`} target="_blank" rel="noreferrer">{me.steam_id} ↗</a>
      </p>
      <a className="ghost-button account-link" href={ACCOUNT_PATH}>Account settings</a>
      {relink && (
        <div className="steam-error relink-alert" role="alert">
          <strong>Sync paused.</strong> {RELINK_TEXT[relink.reason] ?? RELINK_TEXT.invalid_known_code}{" "}
          <a href={ACCOUNT_PATH}>Re-link in Account settings</a>
        </div>
      )}
    </div>
  );
}

/** Account settings (``/account``): match-history codes, auto-sync, disconnect, sign out, delete. */
export function AccountSettings({ me, onChange, onSignedOut }: { me: Me; onChange: () => Promise<unknown>; onSignedOut: () => void }) {
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const access = me.match_access;
  const relink = access.linked ? access.needs_relink ?? null : null;
  const awaitingShare = access.linked && !!access.awaiting_share_code;
  // Sync is blocked on a re-link: move focus to the alert so screen readers announce why
  // (awaiting a first share code focuses that box instead; see LinkForm ``focus``).
  const relinkRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (relink) relinkRef.current?.focus();
  }, [relink?.reason, relink?.field]);  // not the object: /me refreshes would steal focus

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
    if (!window.confirm("Remove your stored Game Authentication Code and share-code cursor? Imported matches stay until you delete your data. To revoke it on Valve’s side too, create a new code on Valve’s match-history page.")) return;
    void run(async () => {
      await steamApi.deleteMatchAccess();
      await onChange();
      setNotice("Match history disconnected. Your imported matches stay.");
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
    <div className="account-settings">
      <div className="steam-card">
        <span className="section-kicker">STEAM ACCOUNT</span>
        <h3>
          SteamID{" "}
          <a href={`https://steamcommunity.com/profiles/${me.steam_id}`} target="_blank" rel="noreferrer">
            {me.steam_id} ↗
          </a>
        </h3>
      </div>

      <div className="steam-card">
        <span className="section-kicker">MATCH HISTORY</span>
        {notice && <div className="steam-notice" role="status">{notice}</div>}
        {access.linked ? (
          <div className="steam-linked">
            <p>
              {awaitingShare ? "Auth saved" : "Linked"} <code>{access.auth_code_hint}</code>
              {access.updated_at && <> · updated {new Date(access.updated_at).toLocaleString()}</>}
            </p>
            <AutoSyncStatus me={me} onChange={onChange} />
            {relink ? (
              <>
                <div className="steam-error relink-alert" role="alert" tabIndex={-1} ref={relinkRef} id="relink-reason">
                  <strong>Re-link needed to keep syncing.</strong> {RELINK_TEXT[relink.reason] ?? RELINK_TEXT.invalid_known_code}
                </div>
                <LinkForm onLinked={linked} mode={relink.field === "auth_code" ? "auth" : "share"} describedBy="relink-reason" />
              </>
            ) : awaitingShare ? (
              <LinkForm onLinked={linked} mode="share" focus hint="Paste a share code from a recent match." />
            ) : (
              <details className="replace-codes">
                <summary>Update codes</summary>
                <LinkForm onLinked={linked} mode="replace" />
              </details>
            )}
            <div className="steam-actions">
              <button type="button" className="ghost-button" onClick={disconnect} disabled={busy}>Disconnect match history</button>
            </div>
          </div>
        ) : (
          <LinkForm onLinked={linked} mode="link" />
        )}
      </div>

      <div className="steam-card">
        <span className="section-kicker">SESSION &amp; DATA</span>
        <div className="steam-actions">
          <button type="button" className="ghost-button" onClick={logout} disabled={busy}>Sign out</button>
          <button type="button" className="danger-button" onClick={deleteAll} disabled={busy}>Delete my data</button>
        </div>
        {error && <div className="steam-error" role="alert">{error}</div>}
      </div>
    </div>
  );
}

/** Why automatic sync isn't running for this user (``auto_sync.paused_reason``). */
export const AUTO_SYNC_PAUSED_TEXT: Record<string, string> = {
  turned_off: "auto-sync off",
  needs_relink: "auto-sync paused until you re-link",
  not_linked: "auto-sync starts once you link",
  needs_share_code: "auto-sync starts once you add a share code",
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
          <span>Auto-sync new matches</span>
        </label>
      )}
      {error && <div className="steam-error" role="alert">{error}</div>}
    </div>
  );
}

type LinkFormProps = {
  onLinked: () => Promise<unknown>;
  /** ``link``: first link (auth + optional share). ``auth`` / ``share``: sync is blocked on that
   * one code (re-link, or the first share code). ``replace``: healthy link — both boxes optional,
   * an empty box keeps the stored code. */
  mode: "link" | "auth" | "share" | "replace";
  /** Sync is blocked on this form: focus its field when the page opens. */
  focus?: boolean;
  /** One short line above the fields (``share`` mode). */
  hint?: string;
  /** Element that explains why this code is needed (the re-link alert). */
  describedBy?: string;
};

function LinkForm({ onLinked, mode, focus = false, hint, describedBy }: LinkFormProps) {
  const [authCode, setAuthCode] = useState("");
  const [shareCode, setShareCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldError, setFieldError] = useState<{ field: "auth" | "share"; text: string } | null>(null);
  const [touched, setTouched] = useState({ auth: false, share: false });
  const authRef = useRef<HTMLInputElement>(null);
  const shareRef = useRef<HTMLInputElement>(null);

  const wantsAuth = mode !== "share";
  const wantsShare = mode !== "auth";
  const keep = mode === "replace";  // empty box keeps the stored code
  const authOptional = !wantsAuth || keep;
  const shareOptional = mode !== "share";  // nobody needs a recent match to link
  const auth = checkAuthCode(authCode, authOptional);
  const share = checkShareCode(shareCode, shareOptional);
  // Consent when storing a (new) auth code; share-code-only updates reuse the consent given at link.
  const needsConsent = wantsAuth && (!keep || !!authCode.trim());
  const ids = { auth: "link-auth-hint", share: "link-share-hint", note: "link-form-note", where: "link-share-where" };
  // Field descriptions: the error/hint first, then why (re-link alert / form note) and where to find it.
  const describe = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ") || undefined;
  const why = describedBy ?? (hint ? ids.note : undefined);

  useEffect(() => {
    if (focus) (mode === "auth" ? authRef : shareRef).current?.focus();
  }, [focus, mode]);

  const authMessage = fieldError?.field === "auth" ? fieldError.text : touched.auth && authCode.trim() ? auth.hint : null;
  const shareMessage = fieldError?.field === "share" ? fieldError.text : touched.share && shareCode.trim() ? share.hint : null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched({ auth: true, share: true });
    setError("");
    setFieldError(null);
    const nothingToSave = keep && !auth.value && !share.value;
    if (!auth.ok || !share.ok || nothingToSave) {
      if (!auth.ok) {
        if (!authCode.trim()) setFieldError({ field: "auth", text: `Paste your Game Authentication Code (${AUTH_CODE_EXAMPLE}).` });
        authRef.current?.focus();
      } else {
        if (!shareCode.trim()) setFieldError({ field: "share", text: "Paste a match sharing code (CSGO-…)." });
        shareRef.current?.focus();
      }
      return;
    }
    setBusy(true);
    try {
      await steamApi.putMatchAccess({ auth_code: auth.value, share_code: share.value, consent: needsConsent ? consent : true });
      setAuthCode("");
      setShareCode("");
      setConsent(false);
      setTouched({ auth: false, share: false });
      await onLinked();
    } catch (reason) {
      const field = reason instanceof ApiError ? errorField(reason.code) : null;
      if (field && (field === "auth" ? wantsAuth : wantsShare)) {
        setFieldError({ field, text: errorText(reason, "Check this code.") });
        (field === "auth" ? authRef : shareRef).current?.focus();
      } else setError(errorText(reason, "Could not link your match history."));
    } finally {
      setBusy(false);
    }
  }

  const foundInLink = share.ok && !!share.value && share.value !== shareCode.trim();
  const submitText = mode === "link" ? "Link" : mode === "share" ? "Save share code" : "Save";
  // Valve checks any code we send (the auth code too), so "Saving…" only when nothing goes to Valve.
  const busyLabel = auth.value || share.value ? "Checking with Valve…" : "Saving…";
  return (
    <form className={`steam-form ${mode === "link" ? "" : "compact"}`} onSubmit={submit} noValidate
      aria-label={keep ? "Update codes" : "Link match history"}>
      {mode === "link" && (
        <>
          <p className="steam-muted">
            Codes from <a href={AUTH_CODE_URL} target="_blank" rel="noreferrer">Valve’s match-history page ↗</a>.
          </p>
          <p className="steam-muted">Imports that match and newer. Expire ~30 days. Older/FACEIT: upload a demo.</p>
        </>
      )}
      {hint && <p className="steam-muted awaiting-share" id={ids.note}>{hint}</p>}
      {wantsAuth && (
        <label className="field">
          <span className="field-label">Auth code</span>
          <span className="number-wrap">
            <input ref={authRef} type="password" autoComplete="off" spellCheck={false} name="auth_code"
              placeholder={keep ? "Keep current" : AUTH_CODE_EXAMPLE} value={authCode}
              aria-invalid={authMessage ? true : undefined} aria-describedby={describe(authMessage && ids.auth, mode === "auth" && why)}
              onChange={(event) => { setAuthCode(event.target.value); if (fieldError?.field === "auth") setFieldError(null); }}
              onBlur={() => setTouched((t) => ({ ...t, auth: true }))} />
          </span>
          {authMessage
            ? <span id={ids.auth} className="field-hint error">{authMessage}</span>
            : auth.ok && auth.value && <span className="field-hint ok">Looks right.</span>}
        </label>
      )}
      {wantsShare && (
        <label className="field">
          <span className="field-label">Share code</span>
          <span className="number-wrap">
            <input ref={shareRef} type="text" autoComplete="off" spellCheck={false} name="share_code"
              placeholder={keep ? "Keep current" : SHARE_CODE_EXAMPLE} value={shareCode}
              aria-invalid={shareMessage ? true : undefined}
              aria-describedby={describe(shareMessage && ids.share, mode === "share" && why, mode === "link" && ids.where)}
              onChange={(event) => { setShareCode(event.target.value); if (fieldError?.field === "share") setFieldError(null); }}
              onBlur={() => setTouched((t) => ({ ...t, share: true }))} />
          </span>
          {shareMessage
            ? <span id={ids.share} className="field-hint error">{shareMessage}</span>
            : share.ok && share.value
              ? <span className="field-hint ok">{foundInLink ? `Found ${share.value} in the link.` : "Looks right."}</span>
              : mode === "link" && <span id={ids.where} className="field-hint">Optional · latest match token there, or CS2 Watch → Your Matches.</span>}
        </label>
      )}
      {needsConsent && (
        <label className="steam-consent">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span>Store encrypted; auto-sync on by default. Disconnect anytime.</span>
        </label>
      )}
      <button className="submit-button" type="submit" disabled={busy || (needsConsent && !consent)}>
        <span>{busy ? busyLabel : submitText}</span><span className="button-arrow">↗</span>
      </button>
      {error && <div className="steam-error" role="alert">{error}</div>}
    </form>
  );
}
