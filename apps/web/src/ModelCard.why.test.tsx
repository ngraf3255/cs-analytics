import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WHY_TIMEOUT_MS, WhyThisRound } from "./ModelCard";

const INPUTS = { mapName: "de_mirage", side: "ct" as const, seconds: "15", weapon: "awp" };

/** fetch that never answers unless aborted (a hung API). */
function hangingFetch() {
  return vi.fn((_: unknown, init: RequestInit = {}) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  }));
}

async function open() {
  fireEvent.click(screen.getByText("Why this call?"));
  const details = document.querySelector("details.why-round") as HTMLDetailsElement;
  details.open = true;
  await act(async () => { details.dispatchEvent(new Event("toggle")); });
}

describe("Why this call? never sticks on 'Asking the model…'", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("a hung API resolves to 'Couldn’t check.' after the timeout", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    render(<WhyThisRound inputs={INPUTS} ct={0.6} weapons={["ak47", "awp", "m4a1_silencer"]} />);
    await open();
    expect(screen.getByText("Asking the model…")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(WHY_TIMEOUT_MS + 10); });
    expect(screen.queryByText("Asking the model…")).toBeNull();
    expect(screen.getAllByText("Couldn’t check.").length).toBeGreaterThan(0);
  });

  it("a new prediction aborts the old asks and resolves for the new inputs", async () => {
    const fetchMock = vi.fn(async (_: unknown, init: RequestInit = {}) => {
      const body = JSON.parse(String(init.body));
      return { ok: true, json: async () => ({ probabilities: { ct: body.opening_kill_side === "t" ? 0.4 : 0.5 } }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<WhyThisRound inputs={INPUTS} ct={0.6} weapons={["ak47"]} />);
    await open();
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    rerender(<WhyThisRound inputs={{ ...INPUTS, seconds: "30" }} ct={0.7} weapons={["ak47"]} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByText("Asking the model…")).toBeNull();
    expect(screen.getByText(/If T had got it instead: CT 40%/)).toBeInTheDocument();
  });
});
