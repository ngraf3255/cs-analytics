import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "./test/fakeApi";
import App from "./App";

const OPTIONS = { maps: ["de_ancient", "de_mirage"], weapons: ["ak47", "awp", "glock", "m4a1_silencer", "aug"], opening_kill_sides: ["ct", "t"] };
const PREDICTION = { predicted_winner: "t", probabilities: { ct: 0.3, t: 0.7 } };

function routes() {
  return {
    "GET /options": { status: 200, body: OPTIONS },
    "POST /predict": { status: 200, body: PREDICTION },
    "GET /steam/status": { status: 200, body: { enabled: false } },
  };
}

describe("round predictor form", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => window.localStorage.clear());

  it("a preset predicts in one tap with the preset's side / time / weapon and keeps the map", async () => {
    const api = installFakeApi(routes());
    render(<App />);
    await advance();
    fireEvent.change(screen.getByLabelText("Map"), { target: { value: "de_mirage" } });
    fireEvent.click(screen.getByRole("button", { name: /AWP pick/ }));
    await advance(400);
    const calls = api.calls.filter((c) => c.method === "POST" && c.path === "/predict");
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toEqual({ map_name: "de_mirage", opening_kill_side: "ct", opening_kill_seconds: 8, opening_weapon: "awp" });
    expect(screen.getByText("Winner")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /AWP pick/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("after the first prediction, edits update the readout by themselves (one request per pause)", async () => {
    const api = installFakeApi(routes());
    render(<App />);
    await advance();
    fireEvent.click(screen.getByRole("button", { name: /Predict/ }));
    await advance(400);
    expect(api.count("POST /predict")).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "30s" }));
    fireEvent.click(screen.getByRole("button", { name: "60s" }));
    await advance(400);
    expect(api.count("POST /predict")).toBe(2);
    const predicts = api.calls.filter((c) => c.path === "/predict");
    expect(predicts[predicts.length - 1]?.body).toMatchObject({ opening_kill_seconds: 60 });
  });

  it("remembers the last-used map and inputs across reloads", async () => {
    installFakeApi(routes());
    const { unmount } = render(<App />);
    await advance();
    fireEvent.change(screen.getByLabelText("Map"), { target: { value: "de_mirage" } });
    fireEvent.click(screen.getByRole("button", { name: /Pistol round/ }));
    await advance(400);
    unmount();
    installFakeApi(routes());
    render(<App />);
    await advance();
    expect(screen.getByLabelText("Map")).toHaveValue("de_mirage");
    expect(screen.getByLabelText("Opening weapon")).toHaveValue("glock");
    expect(screen.getByLabelText("Opening kill time in seconds")).toHaveValue(20);
  });
});
