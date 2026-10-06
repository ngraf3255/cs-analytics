import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { confidenceLabel } from "./ModelCard";
import { advance, installFakeApi, type Call } from "./test/fakeApi";

const OPTIONS = { maps: ["de_mirage", "de_nuke"], weapons: ["ak47", "m4a1_silencer", "awp"], opening_kill_sides: ["ct", "t"] };

// CT chance depends on the inputs so each counterfactual gets its own answer.
function predict(call: Call) {
  const b = call.body as { opening_kill_side: string; opening_kill_seconds: number; opening_weapon: string };
  let ct = b.opening_kill_side === "ct" ? 0.7 : 0.3;
  if (b.opening_kill_seconds > 30) ct -= 0.05;
  if (b.opening_weapon === "ak47") ct -= 0.02;
  return { status: 200, body: { predicted_winner: ct > 0.5 ? "ct" : "t", probabilities: { ct, t: 1 - ct } } };
}

describe("model card, confidence and why this call", () => {
  beforeEach(() => { vi.useFakeTimers(); window.localStorage.clear(); });

  it("confidence words", () => {
    expect(confidenceLabel({ ct: 0.52, t: 0.48 }).label).toBe("Toss-up");
    expect(confidenceLabel({ ct: 0.4, t: 0.6 }).label).toBe("Lean");
    expect(confidenceLabel({ ct: 0.7, t: 0.3 }).label).toBe("Strong lean");
  });

  it("lists the maps the model covers", async () => {
    installFakeApi({ "GET /options": { status: 200, body: OPTIONS }, "GET /steam/status": { status: 200, body: { enabled: false } }, "POST /predict": predict });
    render(<App />);
    await advance();
    const card = screen.getByLabelText("Model card");
    expect(card).toHaveTextContent("MAPS COVERED · 2");
    expect(card).toHaveTextContent("mirage, nuke");
  });

  it("why this call: asks the model about one change at a time, only when opened", async () => {
    const api = installFakeApi({ "GET /options": { status: 200, body: OPTIONS }, "GET /steam/status": { status: 200, body: { enabled: false } }, "POST /predict": predict });
    render(<App />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /CT hold/ }));  // CT · 25s · M4A1-S
    await advance(400);
    expect(screen.getByText("Strong lean")).toBeInTheDocument();
    expect(api.count("POST /predict")).toBe(1);
    const why = screen.getByText("Why this call?").closest("details")!;
    why.open = true;
    fireEvent(why, new Event("toggle"));
    await advance();
    expect(api.count("POST /predict")).toBe(4);
    const list = within(why).getByRole("list", { name: "What moves the estimate" });
    expect(list).toHaveTextContent("If T had got it instead: CT 30% (−40 pts for CT).");
    expect(list).toHaveTextContent("If it had come at 45s: CT 65% (−5 pts for CT).");
    expect(list).toHaveTextContent("With AK-47 instead of M4A1-S: CT 68% (−2 pts for CT).");
  });
});
