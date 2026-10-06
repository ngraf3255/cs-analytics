import { useState } from "react";
import { ACCOUNT_PATH } from "./routes";

/** First-run checklist for a signed-in Steam account with no matches yet: what's done, what's
 * next. Titles only (no body essays). Signed-in only. */
export function FirstRunSteps({ linked, relink, awaitingShare }: { linked: boolean; relink: boolean; awaitingShare: boolean }) {
  const linkState: StepState = linked && !relink && !awaitingShare ? "done" : "next";
  return (
    <ol className="first-run" aria-label="Get started">
      <Step state="done" title="Signed in with Steam" />
      <Step state={linkState} title="Link your match history" optional />
      <Step state={linkState === "done" ? "next" : "todo"} title="Import your first match" />
      <Step state="todo" title="Open the round report" />
    </ol>
  );
}

type StepState = "done" | "next" | "todo";

function Step({ state, title, optional }: { state: StepState; title: string; optional?: boolean }) {
  return (
    <li className={`first-run-step ${state}`}>
      <span className="first-run-mark" aria-hidden="true">{state === "done" ? "✓" : ""}</span>
      <div>
        <strong>{title}{optional && <small> · optional</small>}<span className="sr-only">{state === "done" ? " (done)" : state === "next" ? " (next)" : ""}</span></strong>
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
      <strong>Link match history</strong>
      {" "}
      <a href={ACCOUNT_PATH}>Account settings</a>
      <button type="button" className="tip-dismiss" aria-label="Dismiss tip"
        onClick={() => { try { window.localStorage.setItem(LINK_TIP_KEY, "1"); } catch { /* private mode */ } setHidden(true); }}>✕</button>
    </aside>
  );
}
