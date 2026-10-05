import { ApiError } from "./errors";
import type { MatchAccess, MatchReport, MatchSummary, Me, SyncResult, SyncState, UploadJob } from "./types";

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
    throw new ApiError(0, "api_unreachable");  // our API (e.g. a free instance waking up), not Valve
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : "unknown_error";
    throw new ApiError(response.status, detail);
  }
  return body as T;
}

export type UploadResult = { job: UploadJob };
export type UploadProgress =
  | { phase: "uploading"; fraction: number }
  | { phase: "processing"; job: UploadJob | null };

const JOB_POLL_MS = 2000;
const JOB_ACTIVE = new Set(["queued", "processing"]);
export const isJobActive = (job: UploadJob) => JOB_ACTIVE.has(job.status);

/** POST the raw .dem/.dem.bz2 bytes; the server answers with a background parse job
 * (202, or 200 if the demo is already stored). Uses XHR because fetch() can't report upload progress. */
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
    xhr.upload.onload = () => onProgress?.({ phase: "processing", job: null });
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

/** Poll a parse job every 2 s until it is done or failed (network blips are retried). */
export async function waitForUploadJob(
  job: UploadJob, onUpdate?: (job: UploadJob) => void, signal?: { cancelled: boolean },
): Promise<UploadJob> {
  let current = job;
  let failures = 0;
  while (isJobActive(current)) {
    await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
    if (signal?.cancelled) return current;
    try {
      current = (await request<{ job: UploadJob }>(`/matches/upload/${encodeURIComponent(current.id)}`)).job;
      failures = 0;
      onUpdate?.(current);
    } catch (reason) {
      // e.g. a free-plan instance waking up; give up only after ~1 minute of errors or a real 4xx.
      if ((reason instanceof ApiError && reason.status >= 400 && reason.status < 500) || ++failures > 30) throw reason;
    }
  }
  return current;
}

/** Poll the sync jobs with these ids (one GET /steam/sync per round, 2 s apart) until none is
 * queued or processing. ``onUpdate`` gets the current state of every tracked job, oldest first. */
export async function waitForSyncJobs(
  jobs: UploadJob[], onUpdate?: (jobs: UploadJob[]) => void, signal?: { cancelled: boolean },
): Promise<UploadJob[]> {
  let current = jobs;
  let failures = 0;
  while (current.some(isJobActive)) {
    await new Promise((resolve) => setTimeout(resolve, JOB_POLL_MS));
    if (signal?.cancelled) return current;
    try {
      const latest = new Map((await request<SyncState>("/steam/sync")).jobs.map((job) => [job.id, job]));
      const next: UploadJob[] = [];
      for (const job of current) {
        // Only the newest jobs are listed; look up an active one that dropped off the list directly.
        next.push(latest.get(job.id)
          ?? (isJobActive(job) ? (await request<{ job: UploadJob }>(`/matches/upload/${encodeURIComponent(job.id)}`)).job : job));
      }
      current = next;
      failures = 0;
      onUpdate?.(current);
    } catch (reason) {
      if ((reason instanceof ApiError && reason.status >= 400 && reason.status < 500) || ++failures > 30) throw reason;
    }
  }
  return current;
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
  getSync: () => request<SyncState>("/steam/sync"),
  postSync: () => request<SyncResult>("/steam/sync", { method: "POST" }, true),
  listMatches: (limit = 20, offset = 0) =>
    request<{ matches: MatchSummary[]; limit: number; offset: number }>(`/matches?limit=${limit}&offset=${offset}`),
  uploadDemo,
  waitForUploadJob,
  waitForSyncJobs,
  listUploadJobs: (limit = 5) => request<{ jobs: UploadJob[] }>(`/matches/upload?limit=${limit}`),
  getMatch: (id: string) => request<MatchReport>(`/matches/${encodeURIComponent(id)}`),
};

export { apiBase };
