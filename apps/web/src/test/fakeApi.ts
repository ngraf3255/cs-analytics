/** A scripted stand-in for the cs-analytics API (fetch + the XHR used for uploads).
 *
 * Fixtures in ./fixtures are real responses from the local API (services/prediction_api, SQLite)
 * captured on 2026-10-05: an upload of the demoparser2 test demo (de_mirage, 10 rounds) and a
 * fake-Valve Steam sync of a Valve MM demo (de_ancient, 8 rounds). summary*.json / matches_four.json:
 * GET /matches/summary and the list for a user with 4 imported matches (+ ?recent=2, one match, none).
 * *_personal.json / report_not_in_match.json / summary_not_in_demos.json: per-player analytics for a
 * SteamID that is in the FACEIT de_mirage demo, and for a user in none of their demos (recaptured after
 * warmup / knife rounds were left out: FACEIT 24 rounds). *_outdated.json / upload_updated.json: a match
 * parsed by an older parser version, and the re-upload that updated one. me_auto_sync.json / auto_sync_on|off.json:
 * a user synced by the automatic scheduler (AUTO_SYNC_INTERVAL_SECONDS=120) and PUT /steam/auto-sync.
 * export_rounds_personal.csv: GET /matches/export/rounds.csv for that per-player SteamID (4 matches, 60 rounds). */
import { act } from "@testing-library/react";
import { vi } from "vitest";
import type { UploadJob } from "../steam/types";

export type Reply = { status: number; body?: unknown };
type Handler = (call: Call) => Reply | Promise<Reply>;
export type Call = { method: string; path: string; headers: Headers; body: unknown };

/** Route table keyed by "METHOD /path" (path includes the query string). A value can be a
 * fixed reply, a function, or a list of replies served in order (the last one repeats). */
export function installFakeApi(routes: Record<string, Reply | Reply[] | Handler>) {
  const calls: Call[] = [];
  const served = new Map<string, number>();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://api.test");
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api/, "") + url.search;
    const call: Call = { method, path, headers: new Headers(init.headers), body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const key = `${method} ${path}`;
    const route = routes[key] ?? routes[`${method} ${url.pathname.replace(/^\/api/, "")}`];
    if (route === undefined) throw new Error(`fake API: no route for ${key}`);
    let reply: Reply;
    if (typeof route === "function") reply = await route(call);
    else if (Array.isArray(route)) {
      const n = served.get(key) ?? 0;
      served.set(key, n + 1);
      reply = route[Math.min(n, route.length - 1)];
    } else reply = route;
    if (reply.status === 0) throw new TypeError("Failed to fetch");
    return {
      status: reply.status,
      ok: reply.status >= 200 && reply.status < 300,
      json: async () => (typeof reply.body === "string" ? JSON.parse(reply.body) : reply.body ?? {}),
      blob: async () => new Blob([typeof reply.body === "string" ? reply.body : JSON.stringify(reply.body ?? {})]),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock, count: (key: string) => calls.filter((c) => `${c.method} ${c.path}` === key).length };
}

/** Minimal XMLHttpRequest the upload code talks to; the test drives progress and the reply. */
export class FakeXHR {
  static instances: FakeXHR[] = [];
  method = "";
  url = "";
  headers: Record<string, string> = {};
  withCredentials = false;
  responseType = "";
  status = 0;
  response: unknown = null;
  sent: unknown = null;
  upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void; onload?: () => void } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;

  constructor() { FakeXHR.instances.push(this); }
  open(method: string, url: string) { this.method = method; this.url = url; }
  setRequestHeader(name: string, value: string) { this.headers[name] = value; }
  send(body: unknown) { this.sent = body; }

  progress(loaded: number, total: number) { this.upload.onprogress?.({ lengthComputable: true, loaded, total }); }
  respond(status: number, body: unknown) {
    this.upload.onload?.();
    this.status = status;
    this.response = body;
    this.onload?.();
  }
  fail() { this.onerror?.(); }

  static install() {
    FakeXHR.instances = [];
    vi.stubGlobal("XMLHttpRequest", FakeXHR);
  }
  static last(): FakeXHR {
    const xhr = FakeXHR.instances[FakeXHR.instances.length - 1];
    if (!xhr) throw new Error("no XHR was opened");
    return xhr;
  }
}

/** Let pending promises / effects settle, optionally moving the fake clock (the UI polls every 2 s). */
export async function advance(ms = 0) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

export const POLL_MS = 2000;

export const job = (base: UploadJob, patch: Partial<UploadJob>): UploadJob => ({ ...base, ...patch });
