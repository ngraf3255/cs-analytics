/** Client-side help for the match-history link form: accept codes the way users paste them and
 * say what is wrong before asking Valve. Mirrors steamlink/sharecode.py + valve.normalize_auth_code;
 * the server checks again (and the server's answer wins). */

const DICTIONARY = "ABCDEFGHJKLMNOPQRSTUVWXYZabcdefhijkmnopqrstuvwxyz23456789";
const CHARS = `[${DICTIONARY}]`;
const SHARE_CODE_RE = new RegExp(`^CSGO(-${CHARS}{5}){5}$`);
// No lookbehind: older Safari can't parse it (the whole bundle would fail to load).
const SHARE_CODE_IN_TEXT = new RegExp(`(?:^|[^A-Za-z0-9])(CSGO(?:-${CHARS}{5}){5})(?![A-Za-z0-9])`, "g");
const AUTH_CODE_RE = /^[A-Z0-9]{4}-[A-Z0-9]{5}-[A-Z0-9]{4}$/;
const MAX_SHARE_VALUE = 1n << 144n;

/** Valve's page for creating / revoking the Game Authentication Code; it also shows
 * "Your most recently completed match token" (= a share code). Needs a Steam support sign-in. */
export const AUTH_CODE_URL = "https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128";
/** Valve's developer docs for third-party match-history access (what the two codes allow). */
export const VALVE_MATCH_HISTORY_DOCS_URL = "https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Access_Match_History";
export const SHARE_CODE_GUIDE_URL = "https://leetify.com/blog/share-codes/";

export const AUTH_CODE_EXAMPLE = "ABCD-EFGHI-JKLM";
export const SHARE_CODE_EXAMPLE = "CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx";

function safeDecode(text: string): string {
  try { return decodeURIComponent(text); } catch { return text; }
}

/** Spaces dropped, upper-cased, dashes added back when left out (13 letters/digits). */
export function normalizeAuthCode(text: string): string {
  const code = text.replace(/\s+/g, "").toUpperCase();
  return /^[A-Z0-9]{13}$/.test(code) ? `${code.slice(0, 4)}-${code.slice(4, 9)}-${code.slice(9)}` : code;
}

function inRange(code: string): boolean {
  let value = 0n;
  const chars = code.slice(5).replaceAll("-", "");
  for (let i = chars.length - 1; i >= 0; i--) value = value * BigInt(DICTIONARY.length) + BigInt(DICTIONARY.indexOf(chars[i]));
  return value < MAX_SHARE_VALUE;
}

export const isShareCode = (code: string) => SHARE_CODE_RE.test(code) && inRange(code);
export const isAuthCode = (code: string) => AUTH_CODE_RE.test(code);

/** The one share code in pasted text (bare code or CS2's steam:// share link), else null.
 * Share codes are case-sensitive: never re-cased. */
export function extractShareCode(text: string): string | null {
  const found = new Set([...safeDecode(text.trim()).matchAll(SHARE_CODE_IN_TEXT)].map((m) => m[1]));
  return found.size === 1 ? [...found][0] : null;
}

export type FieldCheck = { value: string; ok: boolean; hint: string | null };

/** ``optional``: already linked, an empty box keeps the stored code. */
export function checkAuthCode(text: string, optional = false): FieldCheck {
  if (!text.trim()) return { value: "", ok: optional, hint: null };
  const value = normalizeAuthCode(text);
  if (isAuthCode(value)) return { value, ok: true, hint: null };
  if (extractShareCode(text)) return { value, ok: false, hint: "That’s a match sharing code (CSGO-…). It goes in the box below; the authentication code looks like ABCD-EFGHI-JKLM." };
  const letters = value.replace(/[^A-Z0-9]/g, "").length;
  return { value, ok: false, hint: `Expected 13 letters and digits as ${AUTH_CODE_EXAMPLE}${letters ? ` (this has ${letters})` : ""}. Copy it from Valve’s page.` };
}

/** ``optional``: an empty box is fine (link the auth code now, add a share code after a match). */
export function checkShareCode(text: string, optional = false): FieldCheck {
  if (!text.trim()) return { value: "", ok: optional, hint: null };
  const value = extractShareCode(text) ?? text.trim();
  if (isShareCode(value)) return { value, ok: true, hint: null };
  if (isAuthCode(normalizeAuthCode(text))) return { value, ok: false, hint: "That’s your Game Authentication Code. This box needs a match sharing code that starts with CSGO-." };
  if (/^csgo-/i.test(value) && !value.startsWith("CSGO-")) return { value, ok: false, hint: "Share codes are case-sensitive and start with CSGO- in capitals. Paste it exactly as copied." };
  return { value, ok: false, hint: `Expected ${SHARE_CODE_EXAMPLE} (5 groups of 5 after CSGO-), or paste the whole steam:// share link.` };
}

/** Which box a server error belongs to (shown under that field). */
export function errorField(code: string): "auth" | "share" | null {
  if (["invalid_auth_code_format", "invalid_auth_code", "auth_code_is_share_code", "auth_code_required", "credentials_unreadable"].includes(code)) return "auth";
  if (["invalid_share_code_format", "invalid_share_code", "share_code_is_auth_code", "share_code_required"].includes(code)) return "share";
  return null;
}
