import { describe, expect, it } from "vitest";
import { weaponName } from "./weapons";

describe("weaponName", () => {
  it("maps CS2 weapon codes to display names", () => {
    expect(weaponName("m4a1_silencer")).toBe("M4A1-S");
    expect(weaponName("m4a1")).toBe("M4A4");  // CS2's code for the M4A4
    expect(weaponName("usp_silencer")).toBe("USP-S");
    expect(weaponName("galilar")).toBe("Galil AR");
    expect(weaponName("mac10")).toBe("MAC-10");
    expect(weaponName("ak47")).toBe("AK-47");
    expect(weaponName("deagle")).toBe("Desert Eagle");
    expect(weaponName("knife_karambit")).toBe("Karambit");
    expect(weaponName("weapon_awp")).toBe("AWP");
    expect(weaponName("AWP")).toBe("AWP");
  });

  it("prettifies unknown codes instead of showing them raw", () => {
    expect(weaponName("knife_new_thing")).toBe("New Thing Knife");
    expect(weaponName("mp12x")).toBe("MP12X");
    expect(weaponName("laser_rifle")).toBe("Laser Rifle");
    expect(weaponName(null)).toBe("?");
    expect(weaponName("")).toBe("?");
  });
});
