import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import reportPersonal from "../test/fixtures/report_personal.json";
import reportFixture from "../test/fixtures/report.json";
import summaryPersonal from "../test/fixtures/summary_personal.json";
import { ShareButton } from "./ShareButton";
import { matchCard, profileCard } from "./shareCard";
import type { MatchReport, MatchesAnalytics } from "./types";

describe("share card content", () => {
  it("match you played: result headline and your own numbers", () => {
    const card = matchCard(reportPersonal as MatchReport);
    expect(card.title).toBe("mirage");
    expect(card.headline).toBe("Won 13–11");
    expect(card.tone).toBe("won");
    expect(card.stats.map((s) => s.label)).toEqual(["KILLS / DEATHS", "K/D RATIO", "ROUNDS WON", "OPENING DUELS"]);
    expect(card.stats[0].value).toBe("29 / 17");
    expect(card.fileName).toMatch(/^csgooner-mirage-.*\.png$/);
  });

  it("demo you're not in: match score and rounds only (nothing about you)", () => {
    const card = matchCard(reportFixture as MatchReport);
    expect(card.tone).toBe("neutral");
    expect(card.stats.map((s) => s.label)).toEqual(["ROUNDS", "MODEL CALLED"]);
  });

  it("profile numbers from the summary", () => {
    const you = (summaryPersonal as MatchesAnalytics).you!;
    const card = profileCard(you);
    expect(card.headline).toBe(`${you.results.won}–${you.results.lost}${you.results.tied ? `–${you.results.tied}` : ""}`);
    expect(card.stats).toHaveLength(4);
  });
});

describe("share button", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the share sheet with the PNG when files can be shared", async () => {
    const toBlob = vi.fn((cb: BlobCallback) => cb(new Blob(["png"], { type: "image/png" })));
    const ctx = new Proxy({}, { get: (_t, key) => (key === "canvas" ? undefined : () => undefined), set: () => true });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(toBlob);
    const share = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, share, canShare: () => true });
    render(<ShareButton card={() => matchCard(reportPersonal as MatchReport)} label="Share match" />);
    fireEvent.click(screen.getByRole("button", { name: /Share match/ }));
    expect(await screen.findByText("Image ready.")).toBeInTheDocument();
    const files = (share.mock.calls[0] as unknown as [{ files: File[] }])[0].files;
    expect(files[0].type).toBe("image/png");
    expect(files[0].name).toMatch(/^csgooner-mirage/);
  });

  it("says so when the browser can't draw the image", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    render(<ShareButton card={() => matchCard(reportPersonal as MatchReport)} />);
    fireEvent.click(screen.getByRole("button", { name: /Share image/ }));
    expect(await screen.findByText("Couldn’t make the image in this browser.")).toBeInTheDocument();
  });
});
