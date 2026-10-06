import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { advance, installFakeApi } from "../test/fakeApi";
import exportRounds from "../test/fixtures/export_rounds_personal.csv?raw";
import meFixture from "../test/fixtures/me_personal.json";
import { downloadExport } from "./api";
import { ExportPage } from "./ExportPage";
import { TableauExport } from "./TableauExport";
import type { Me } from "./types";

const me = meFixture as Me;
const MATCHES_CSV = "match_id,map_name\r\nabc,de_mirage\r\n";

function routes(extra: Parameters<typeof installFakeApi>[0] = {}) {
  return {
    "GET /steam/status": { status: 200, body: { steam: true, upload: true, guest: true } },
    "GET /me": { status: 200, body: me },
    "GET /matches/export/rounds.csv": { status: 200, body: exportRounds },
    "GET /matches/export/matches.csv": { status: 200, body: MATCHES_CSV },
    ...extra,
  };
}

const blobText = (blob: Blob) => new Promise<string>((resolve) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result));
  reader.readAsText(blob);
});
const group = () => screen.getByRole("group", { name: "Export for Tableau" });

let saved: { href: string; download: string }[];
let blobs: Blob[];

beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState(null, "", "/account/export");
  saved = [];
  blobs = [];
  let n = 0;
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: vi.fn((blob: Blob) => { blobs.push(blob); return `blob:test/${++n}`; }),
    revokeObjectURL: vi.fn(),
  }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ href: this.href, download: this.download });
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

describe("Export for Tableau (/account/export)", () => {
  it("page layout: downloads rounds CSV with the session cookie", async () => {
    const api = installFakeApi(routes());
    render(<ExportPage />);
    await advance();
    expect(screen.queryByText("EXPORT FOR TABLEAU")).not.toBeInTheDocument();
    expect(group()).not.toHaveTextContent(/ROUNDS ·|MATCHES ·/);
    fireEvent.click(within(group()).getByRole("button", { name: "Rounds CSV" }));
    await advance();
    expect(api.count("GET /matches/export/rounds.csv")).toBe(1);
    const init = api.fetchMock.mock.calls.find(([url]) => String(url).endsWith("/matches/export/rounds.csv"))?.[1];
    expect(init?.credentials).toBe("include");
    expect(saved).toHaveLength(1);
    expect(saved[0].download).toMatch(/^cs2-rounds-\d{8}\.csv$/);
    expect(saved[0].href).toBe("blob:test/1");
    const reading = blobText(blobs[0]);
    await advance(10);
    const text = await reading;
    const [header, first] = text.split("\r\n");
    expect(header.split(",").slice(0, 4)).toEqual(["match_id", "map_name", "match_date", "match_day"]);
    expect(header).toContain("model_ct_win_probability");
    expect(header).toContain("you_side");
    expect(first).toContain(",de_mirage,");
    expect(within(group()).getByRole("status")).toHaveTextContent(/Downloaded cs2-rounds-\d{8}\.csv\./);
    await advance(1000);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test/1");
  });

  it("downloads the matches CSV too, and disables actions while preparing", async () => {
    let release: () => void = () => undefined;
    const api = installFakeApi(routes({
      "GET /matches/export/matches.csv": () => new Promise((resolve) => { release = () => resolve({ status: 200, body: MATCHES_CSV }); }),
    }));
    render(<ExportPage />);
    await advance();
    fireEvent.click(within(group()).getByRole("button", { name: "Matches CSV" }));
    await advance();
    expect(within(group()).getByRole("button", { name: "Rounds CSV" })).toBeDisabled();
    release();
    await advance();
    expect(api.count("GET /matches/export/matches.csv")).toBe(1);
    expect(saved.map((s) => s.download)).toEqual([expect.stringMatching(/^cs2-matches-\d{8}\.csv$/)]);
    expect(within(group()).getByRole("button", { name: "Matches CSV" })).toBeEnabled();
  });

  it("shows the API's error (e.g. signed out) and saves nothing", async () => {
    installFakeApi(routes({ "GET /matches/export/rounds.csv": { status: 401, body: { detail: "not_authenticated" } } }));
    render(<ExportPage />);
    await advance();
    fireEvent.click(within(group()).getByRole("button", { name: "Rounds CSV" }));
    await advance();
    expect(saved).toEqual([]);
    expect(within(group()).getByRole("status")).toHaveTextContent("Export failed. Sign in to continue.");
  });

  it("api.downloadExport: unreachable API -> api_unreachable; file name uses the UTC date", async () => {
    installFakeApi({ "GET /matches/export/rounds.csv": { status: 0 } });
    await expect(downloadExport("rounds")).rejects.toMatchObject({ code: "api_unreachable" });
    installFakeApi({ "GET /matches/export/matches.csv": { status: 200, body: MATCHES_CSV } });
    await expect(downloadExport("matches", new Date("2026-10-05T23:30:00Z"))).resolves.toBe("cs2-matches-20261005.csv");
  });

  it("menu layout: ⋮ still works for embeds; Download both fetches both files", async () => {
    const api = installFakeApi(routes());
    render(<TableauExport layout="menu" />);
    await advance();
    fireEvent.click(within(group()).getByRole("button", { name: "Export menu" }));
    const menu = within(group()).getByRole("menu", { name: "Export for Tableau" });
    expect(within(menu).getByRole("menuitem", { name: "Rounds CSV" })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /Tableau help/ })).toHaveAttribute("href", expect.stringContaining("tableau/README.md"));
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Download both" }));
    await advance();
    await advance();
    expect(api.count("GET /matches/export/rounds.csv")).toBe(1);
    expect(api.count("GET /matches/export/matches.csv")).toBe(1);
    expect(saved.map((f) => f.download)).toEqual([expect.stringMatching(/^cs2-rounds-/), expect.stringMatching(/^cs2-matches-/)]);
    expect(within(group()).getByRole("status")).toHaveTextContent(/Downloaded cs2-rounds-\d{8}\.csv and cs2-matches-\d{8}\.csv\./);
  });

  it("menu closes on Escape", async () => {
    installFakeApi(routes());
    render(<TableauExport layout="menu" />);
    await advance();
    fireEvent.click(within(group()).getByRole("button", { name: "Export menu" }));
    expect(within(group()).getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(within(group()).queryByRole("menu")).toBeNull();
    expect(within(group()).getByRole("button", { name: "Export menu" })).toHaveAttribute("aria-expanded", "false");
  });
});
