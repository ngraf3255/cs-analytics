import { useState } from "react";
import { shareCard, type ShareCard } from "./shareCard";

/** One tap: builds the PNG and opens the share sheet (phones) or downloads it. */
export function ShareButton({ card, label = "Share image" }: { card: () => ShareCard; label?: string }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  async function onClick() {
    setState("busy");
    try {
      const result = await shareCard(card());
      setState(result === "cancelled" ? "idle" : "done");
    } catch {
      setState("error");
    }
  }
  return (
    <span className="share-wrap">
      <button type="button" className="ghost-button share-button" onClick={() => void onClick()} disabled={state === "busy"}>
        {state === "busy" ? "Making image…" : `${label} ↗`}
      </button>
      <span className="share-status" aria-live="polite">
        {state === "done" ? "Image ready." : state === "error" ? "Couldn’t make the image in this browser." : ""}
      </span>
    </span>
  );
}
