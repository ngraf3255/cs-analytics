import { useState, type ReactNode } from "react";
import { ACCOUNT_PATH } from "./routes";

/** First-run checklist for a signed-in Steam account with no matches yet: what's done, what's
 * next, and both ways to get a first report (sync from Steam or upload a demo). Signed-in only. */
export function FirstRunSteps({ linked, relink, awaitingShare }: { linked: boolean; relink: boolean; awaitingShare: boolean }) {
  const linkState: StepState = linked && !relink && !awaitingShare ? "done" : "next";
  return (
    <ol className="first-run" aria-label="Get started">
      <Step state="done" title="Signed in with Steam" />
      <Step state={linkState} title="Link your match history" optional>
        {relink
          ? <>Sync is paused: update your codes in <a href={ACCOUNT_PATH}>Account settings</a>.</>
          : awaitingShare
            ? <>Add a share code in <a href={ACCOUNT_PATH}>Account settings</a> after your next match.</>
            : linked
              ? null
              : <>Paste your codes in <a href={ACCOUNT_PATH}>Account settings</a>. Or skip and upload.</>}
      </Step>
      <Step state={linkState === "done" ? "next" : "todo"} title="Import your first match">
        {linkState === "done" ? "Press Sync matches above, or upload a demo below." : "Upload a demo below now, or sync once your history is linked."}
      </Step>
      <Step state="todo" title="Open the round report">Tap a match to see every round, your opening duels and the model’s call.</Step>
    </ol>
  );
}

type StepState = "done" | "next" | "todo";

function Step({ state, title, optional, children }: { state: StepState; title: string; optional?: boolean; children?: ReactNode }) {
  return (
    <li className={`first-run-step ${state}`}>
      <span className="first-run-mark" aria-hidden="true">{state === "done" ? "✓" : ""}</span>
      <div>
        <strong>{title}{optional && <small> · optional</small>}<span className="sr-only">{state === "done" ? " (done)" : state === "next" ? " (next)" : ""}</span></strong>
        {state !== "done" && children != null && children !== false && <p>{children}</p>}
      </div>
    </li>
  );
}

const LINK_TIP_KEY = "csa.tip.link-history.dismissed";

function dismissed(key: string): boolean {
  try { return window.localStorage.getItem(key) === "1"; } catch { return false; }
}

/** Has matches (e.g. uploads) but no linked match history: one dismissible nudge toward auto-sync. */
export function LinkHistoryTip() {
  const [hidden, setHidden] = useState(() => dismissed(LINK_TIP_KEY));
  if (hidden) return null;
  return (
    <aside className="onboarding-tip" aria-label="Tip">
      <p><strong>Skip the uploads.</strong> Link match history in <a href={ACCOUNT_PATH}>Account settings</a> for auto-import.</p>
      <button type="button" className="tip-dismiss" aria-label="Dismiss tip"
        onClick={() => { try { window.localStorage.setItem(LINK_TIP_KEY, "1"); } catch { /* private mode */ } setHidden(true); }}>✕</button>
    </aside>
  );
}
