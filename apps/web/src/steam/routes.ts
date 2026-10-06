/** Account settings: match-history codes, auto-sync, sign out, delete data. */
export const ACCOUNT_PATH = "/account";
/** Tableau CSV export (kept off the home page). */
export const ACCOUNT_EXPORT_PATH = "/account/export";
/** Round-winner predictor + readout. */
export const PREDICT_PATH = "/predict";
/** Model card / held-out accuracy. */
export const ABOUT_PATH = "/about";
/** Lobby compare, roles, map tables, all-players analytics. */
export const STATS_PATH = "/stats";

const strip = (pathname: string) => pathname.replace(/\/+$/, "") || "/";

export const isAccountPath = (pathname: string) => strip(pathname) === ACCOUNT_PATH;
export const isAccountExportPath = (pathname: string) => strip(pathname) === ACCOUNT_EXPORT_PATH;
export const isPredictPath = (pathname: string) => strip(pathname) === PREDICT_PATH;
export const isAboutPath = (pathname: string) => strip(pathname) === ABOUT_PATH;
export const isStatsPath = (pathname: string) => strip(pathname) === STATS_PATH;

export type AppPage = "home" | "predict" | "stats" | "about" | "account" | "export";

/** Resolve the SPA page from a pathname (trailing slash ignored). */
export function pageFromPath(pathname: string): AppPage {
  const path = strip(pathname);
  if (path === ACCOUNT_EXPORT_PATH) return "export";
  if (path === ACCOUNT_PATH) return "account";
  if (path === PREDICT_PATH) return "predict";
  if (path === ABOUT_PATH) return "about";
  if (path === STATS_PATH) return "stats";
  return "home";
}
