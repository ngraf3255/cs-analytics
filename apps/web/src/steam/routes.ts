/** Account settings page: match-history codes, auto-sync, sign out, delete data. */
export const ACCOUNT_PATH = "/account";

export const isAccountPath = (pathname: string) => pathname.replace(/\/+$/, "") === ACCOUNT_PATH;
