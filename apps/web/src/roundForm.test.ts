import { afterEach, describe, expect, it } from "vitest";
import { initialInputs, loadRemembered, remember, validSeconds } from "./roundForm";

const MAPS = ["de_ancient", "de_mirage"];
const WEAPONS = ["aug", "ak47", "awp"];

describe("round form defaults", () => {
  afterEach(() => window.localStorage.clear());

  it("defaults: first map, AK-47 when known, CT, 15s", () => {
    expect(initialInputs(MAPS, WEAPONS, {})).toEqual({ mapName: "de_ancient", side: "ct", seconds: "15.0", weapon: "ak47" });
    expect(initialInputs(MAPS, ["aug"], {}).weapon).toBe("aug");
  });

  it("remembers the last-used inputs", () => {
    remember({ mapName: "de_mirage", side: "t", seconds: "8", weapon: "awp" });
    expect(loadRemembered()).toEqual({ mapName: "de_mirage", side: "t", seconds: "8", weapon: "awp" });
    expect(initialInputs(MAPS, WEAPONS)).toEqual({ mapName: "de_mirage", side: "t", seconds: "8", weapon: "awp" });
  });

  it("ignores remembered values the model no longer knows, and corrupt storage", () => {
    expect(initialInputs(MAPS, WEAPONS, { mapName: "de_cache", weapon: "negev", seconds: "500", side: "x" as never }))
      .toEqual({ mapName: "de_ancient", side: "ct", seconds: "15.0", weapon: "ak47" });
    window.localStorage.setItem("csa.round.v1", "{not json");
    expect(loadRemembered()).toEqual({});
  });

  it("validSeconds", () => {
    expect(validSeconds("0")).toBe(true);
    expect(validSeconds("120")).toBe(true);
    expect(validSeconds("")).toBe(false);
    expect(validSeconds("121")).toBe(false);
  });
});
