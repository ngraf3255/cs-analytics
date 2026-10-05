import { ApiError } from "./errors";
import type { MatchAccess, MatchReport, MatchSummary, Me, SyncResult, SyncStatus } from "./types";

const apiBase = (
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? "/api" : "https://cs-analytics-cwmo.onrender.com")
).replace(/\/$/, "");

async function request<T>(path: string, init: RequestInit = {}, mutating = false): Promise<T> {
  const headers = new Headers(init.headers);
  if (mutating) headers.set("X-Requested-With", "csa");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, "valve_unavailable");
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : "unknown_error";
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export type UploadResult = { match: MatchSummary; created: boolean };
export type UploadProgress = { phase: "uploading"; fraction: number } | { phase: "processing" };

/** POST the raw .dem/.dem.bz2 bytes. Uses XHR because fetch() can't report upload progress. */
export function uploadDemo(file: File, onProgress?: (progress: UploadProgress) => void): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiBase}/matches/upload`);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-Requested-With", "csa");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.({ phase: "uploading", fraction: event.loaded / event.total });
    };
    xhr.upload.onload = () => onProgress?.({ phase: "processing" });
    xhr.onerror = () => reject(new ApiError(0, "upload_network_error"));
    xhr.onabort = () => reject(new ApiError(0, "upload_network_error"));
    xhr.onload = () => {
      const body = xhr.response ?? {};
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as UploadResult);
        return;
      }
      const detail = typeof body?.detail === "string" ? body.detail : xhr.status === 413 ? "demo_too_large" : "unknown_error";
      reject(new ApiError(xhr.status, detail));
    };
    xhr.send(file);
  });
}

export function steamLoginUrl(next = "/#matches"): string {
  const path = next.startsWith("/") ? next : `/${next}`;
  return `${apiBase}/auth/steam/login?next=${encodeURIComponent(path)}`;
}

export const steamApi = {
  status: () => request<{ enabled: boolean }>("/steam/status"),
  me: () => request<Me>("/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }, true),
  deleteMe: () => request<void>("/me", { method: "DELETE" }, true),
  putMatchAccess: (body: { auth_code: string; share_code: string; consent: boolean }) =>
    request<MatchAccess>("/steam/match-access", { method: "PUT", body: JSON.stringify(body) }, true),
  deleteMatchAccess: () => request<void>("/steam/match-access", { method: "DELETE" }, true),
  getSync: () => request<SyncStatus>("/steam/sync"),
  postSync: () => request<SyncResult>("/steam/sync", { method: "POST" }, true),
  listMatches: (limit = 20, offset = 0) =>
    request<{ matches: MatchSummary[]; limit: number; offset: number }>(`/matches?limit=${limit}&offset=${offset}`),
  uploadDemo,
  getMatch: (id: string) => request<MatchReport>(`/matches/${encodeURIComponent(id)}`),
};

export { apiBase };
