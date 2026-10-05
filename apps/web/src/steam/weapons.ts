/** Display names for the weapon codes CS2 demos record (player_death "weapon", without the
 * "weapon_" prefix), e.g. m4a1_silencer -> "M4A1-S". Note CS2 calls the M4A4 "m4a1". */
const WEAPON_NAMES: Record<string, string> = {
  // Pistols
  glock: "Glock-18", hkp2000: "P2000", usp_silencer: "USP-S", usp_silencer_off: "USP-S", p250: "P250",
  elite: "Dual Berettas", fiveseven: "Five-SeveN", tec9: "Tec-9", cz75a: "CZ75-Auto", deagle: "Desert Eagle",
  revolver: "R8 Revolver",
  // SMGs
  mac10: "MAC-10", mp9: "MP9", mp7: "MP7", mp5sd: "MP5-SD", ump45: "UMP-45", p90: "P90", bizon: "PP-Bizon",
  // Rifles
  galilar: "Galil AR", famas: "FAMAS", ak47: "AK-47", m4a1: "M4A4", m4a1_silencer: "M4A1-S",
  m4a1_silencer_off: "M4A1-S", sg556: "SG 553", aug: "AUG", ssg08: "SSG 08", awp: "AWP", g3sg1: "G3SG1",
  scar20: "SCAR-20",
  // Heavy
  nova: "Nova", xm1014: "XM1014", sawedoff: "Sawed-Off", mag7: "MAG-7", m249: "M249", negev: "Negev",
  // Grenades and other
  hegrenade: "HE Grenade", flashbang: "Flashbang", smokegrenade: "Smoke Grenade", decoy: "Decoy Grenade",
  molotov: "Molotov", incgrenade: "Incendiary Grenade", inferno: "Molotov / Incendiary fire",
  taser: "Zeus x27", c4: "C4", planted_c4: "C4", world: "World (fall / map damage)",
  // Knives
  knife: "Knife", knife_t: "Knife", knifegg: "Golden Knife", bayonet: "Bayonet", knife_css: "Classic Knife",
  knife_flip: "Flip Knife", knife_gut: "Gut Knife", knife_karambit: "Karambit", knife_m9_bayonet: "M9 Bayonet",
  knife_tactical: "Huntsman Knife", knife_falchion: "Falchion Knife", knife_survival_bowie: "Bowie Knife",
  knife_butterfly: "Butterfly Knife", knife_push: "Shadow Daggers", knife_cord: "Paracord Knife",
  knife_canis: "Survival Knife", knife_ursus: "Ursus Knife", knife_gypsy_jackknife: "Navaja Knife",
  knife_outdoor: "Nomad Knife", knife_stiletto: "Stiletto Knife", knife_widowmaker: "Talon Knife",
  knife_skeleton: "Skeleton Knife", knife_kukri: "Kukri Knife",
};

/** "m4a1_silencer" -> "M4A1-S"; unknown codes are prettified ("knife_new_thing" -> "New Thing Knife",
 * "mp12x" -> "MP12X"); null/empty -> "?". */
export function weaponName(code: string | null | undefined): string {
  if (!code) return "?";
  const key = code.trim().toLowerCase().replace(/^weapon_/, "");
  if (WEAPON_NAMES[key]) return WEAPON_NAMES[key];
  const knife = key.startsWith("knife_");
  const words = (knife ? key.slice("knife_".length) : key).split(/[_\s-]+/).filter(Boolean);
  if (!words.length) return code;
  const pretty = words.map((w) => (/\d/.test(w) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))).join(" ");
  return knife ? `${pretty} Knife` : pretty;
}
