/** Round-predictor form helpers: remembered inputs, quick presets and sensible defaults. */

export type RoundInputs = { mapName: string; side: "ct" | "t"; seconds: string; weapon: string };

export type Preset = { id: string; label: string; detail: string; side: "ct" | "t"; seconds: number; weapon: string };

/** One-tap scenarios (the map stays as picked). Only shown when the model knows the weapon. */
export const PRESETS: Preset[] = [
  { id: "t-entry", label: "T entry", detail: "AK-47 · 15s", side: "t", seconds: 15, weapon: "ak47" },
  { id: "ct-hold", label: "CT hold", detail: "M4A1-S · 25s", side: "ct", seconds: 25, weapon: "m4a1_silencer" },
  { id: "awp-pick", label: "AWP pick", detail: "CT · 8s", side: "ct", seconds: 8, weapon: "awp" },
  { id: "pistol", label: "Pistol round", detail: "Glock · 20s", side: "t", seconds: 20, weapon: "glock" },
  { id: "late-push", label: "Late T push", detail: "AK-47 · 60s", side: "t", seconds: 60, weapon: "ak47" },
];

export const TIME_CHIPS = [5, 15, 30, 60];

/** Listed first in the weapon picker (in this order) when the model knows them. */
export const COMMON_WEAPONS = ["ak47", "m4a1_silencer", "m4a1", "awp", "glock", "usp_silencer", "deagle", "famas", "galilar"];

const STORAGE_KEY = "csa.round.v1";

export function loadRemembered(): Partial<RoundInputs> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};  // private mode / storage disabled / corrupt value
  }
}

export function remember(inputs: RoundInputs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(inputs));
  } catch {
    /* storage unavailable: just don't remember */
  }
}

/** Starting inputs: the last-used ones if the model still knows them, else the first map, AK-47 (or the first weapon), CT, 15s. */
export function initialInputs(maps: string[], weapons: string[], saved: Partial<RoundInputs> = loadRemembered()): RoundInputs {
  const seconds = Number(saved.seconds);
  return {
    mapName: saved.mapName && maps.includes(saved.mapName) ? saved.mapName : maps[0] ?? "",
    side: saved.side === "t" || saved.side === "ct" ? saved.side : "ct",
    seconds: saved.seconds !== undefined && Number.isFinite(seconds) && seconds >= 0 && seconds <= 120 ? String(saved.seconds) : "15.0",
    weapon: saved.weapon && weapons.includes(saved.weapon) ? saved.weapon : weapons.includes("ak47") ? "ak47" : weapons[0] ?? "",
  };
}

export function validSeconds(value: string): boolean {
  if (value.trim() === "") return false;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 && n <= 120;
}
