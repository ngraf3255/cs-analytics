import { render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { steamApi, steamLoginUrl } from "./steam/api";
import { useHashScroll } from "./steam/hooks";
import { pageFromPath } from "./steam/routes";
import { consumeAuthMarker, withAuthMarker } from "./steam/sessionHint";
import { installFakeApi } from "./test/fakeApi";

const HINT = "csa_has_session";
const OPTIONS = { maps: ["de_mirage"], weapons: ["ak47"], opening_kill_sides: ["ct", "t"] };

describe("Steam sign-in marker", () => {
  beforeEach(() => { window.localStorage.clear(); });
  afterEach(() => { window.history.replaceState(null, "", "/"); });

  it("sign-in links return with ?auth=1 before the hash", () => {
    expect(withAuthMarker("/#matches")).toBe("/?auth=1#matches");
    expect(withAuthMarker("/account")).toBe("/account?auth=1");
    expect(withAuthMarker("/x?a=b#y")).toBe("/x?a=b&auth=1#y");
    expect(decodeURIComponent(steamLoginUrl("/#matches"))).toContain("next=/?auth=1#matches");
  });

  it("auth=1 sets the hint before render and is stripped (hash kept)", () => {
    window.history.replaceState(null, "", "/?auth=1#matches");
    consumeAuthMarker();
    expect(window.localStorage.getItem(HINT)).toBe("1");
    expect(window.location.pathname + window.location.search + window.location.hash).toBe("/#matches");
  });

  it("no marker: nothing changes", () => {
    window.history.replaceState(null, "", "/stats?x=1");
    consumeAuthMarker();
    expect(window.localStorage.getItem(HINT)).toBeNull();
    expect(window.location.search).toBe("?x=1");
  });
});

describe("unknown routes", () => {
  afterEach(() => { window.history.replaceState(null, "", "/"); });
  it("pageFromPath: known routes kept, others not found", () => {
    for (const [p, page] of [["/", "home"], ["/stats", "stats"], ["/about/", "about"], ["/account", "account"], ["/account/export", "export"], ["/predict", "home"]]) {
      expect(pageFromPath(p)).toBe(page);
    }
    expect(pageFromPath("/logout")).toBe("notfound");
  });
  it("/logout renders 'Not found · ← Home' and no predictor", () => {
    vi.useFakeTimers();
    window.history.replaceState(null, "", "/logout");
    installFakeApi({ "GET /options": { status: 200, body: OPTIONS }, "GET /steam/status": { status: 200, body: { enabled: false } } });
    render(<App />);
    const nf = screen.getByRole("region", { name: "Not found" });
    expect(nf).toHaveTextContent("Not found · ← Home");
    expect(screen.getByRole("link", { name: "← Home" })).toHaveAttribute("href", "/");
    expect(document.getElementById("predictor")).toBeNull();
  });
  it("Worker: SPA routes 200, unknown paths 404 with the app shell", async () => {
    const path = "../worker/index.js";
    const worker = (await import(/* @vite-ignore */ path)).default as { fetch: (r: Request, env: unknown) => Promise<Response> };
    const env = { ASSETS: { fetch: async () => new Response("<html>app</html>", { status: 200, headers: { "content-type": "text/html" } }) } };
    for (const p of ["/", "/stats", "/stats/", "/about", "/account", "/account/export", "/predict"]) {
      expect((await worker.fetch(new Request(`https://csgooner.com${p}`), env)).status).toBe(200);
    }
    const nf = await worker.fetch(new Request("https://csgooner.com/logout"), env);
    expect(nf.status).toBe(404);
    expect(await nf.text()).toBe("<html>app</html>");
  });
});

describe("/me: one shared soft request", () => {
  it("concurrent callers share one GET /me?soft=1; {account:null} is treated as signed out", async () => {
    const api = installFakeApi({ "GET /me?soft=1": { status: 200, body: { account: null } } });
    const results = await Promise.allSettled([steamApi.me(), steamApi.me(), steamApi.me()]);
    expect(api.count("GET /me")).toBe(1);
    expect(api.calls[0].path).toBe("/me?soft=1");
    for (const r of results) expect(r.status === "rejected" && (r.reason as { status: number }).status).toBe(401);
  });
});

describe("#matches anchor re-pins while layout settles", () => {
  let resize: () => void = () => undefined;
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { constructor(cb: () => void) { resize = cb; } observe() {} disconnect() {} });
    window.history.replaceState(null, "", "/#matches");
    document.body.innerHTML = '<section id="matches"></section>';
  });
  afterEach(() => { window.history.replaceState(null, "", "/"); document.body.innerHTML = ""; });

  it("scrolls again on resize, and stops after the first user scroll", () => {
    const el = document.getElementById("matches")!;
    const spy = vi.fn(); el.scrollIntoView = spy;
    renderHook(() => useHashScroll(true));
    expect(spy).toHaveBeenCalledTimes(1);
    resize();
    expect(spy).toHaveBeenCalledTimes(2);
    window.dispatchEvent(new Event("wheel"));
    resize();
    expect(spy).toHaveBeenCalledTimes(2);
  });
});
