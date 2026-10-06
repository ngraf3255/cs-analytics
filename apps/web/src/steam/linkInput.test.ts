import { describe, expect, it } from "vitest";
import { checkAuthCode, checkShareCode, errorField, extractShareCode, isShareCode, normalizeAuthCode } from "./linkInput";

// Same cases as services/prediction_api/tests/test_link_input.py (the server checks again).
const SHARE = "CSGO-9Dyih-7YcBA-VXraV-tRjNt-A3VTB";  // a real code from the local stack's fake history
const LINK = `steam://rungame/730/76561202255233023/+csgo_download_match%20${SHARE}`;

describe("pasted match-history codes", () => {
  it.each([SHARE, `  ${SHARE}\n`, LINK, LINK.replace("%20", " "), `"${SHARE}"`, `Match code: ${SHARE}.`, `${SHARE} ${SHARE}`])(
    "finds the share code in %j", (text) => {
      expect(extractShareCode(text)).toBe(SHARE);
    });

  it.each(["", "CSGO-bad", SHARE.toLowerCase(), `${SHARE}x`, `x${SHARE}`, `${SHARE} CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK`, "AB12-CDE34-FG56", "%E0%A4%A"])(
    "finds no single share code in %j", (text) => {
      expect(extractShareCode(text)).toBeNull();
    });

  it("range-checks well-formed share codes like the server", () => {
    expect(isShareCode(SHARE)).toBe(true);
    expect(isShareCode(`CSGO-${Array(5).fill("99999").join("-")}`)).toBe(false);
  });

  it.each([
    ["AB12-CDE34-FG56", "AB12-CDE34-FG56"], ["ab12-cde34-fg56", "AB12-CDE34-FG56"],
    [" AB12 CDE34 FG56 ", "AB12-CDE34-FG56"], ["ab12cde34fg56", "AB12-CDE34-FG56"], ["AB12-CDE34", "AB12-CDE34"],
  ])("normalises the auth code %j", (text, expected) => {
    expect(normalizeAuthCode(text)).toBe(expected);
  });

  it("explains what is wrong with each box, including codes pasted into the wrong one", () => {
    expect(checkAuthCode("ab12cde34fg56")).toEqual({ value: "AB12-CDE34-FG56", ok: true, hint: null });
    expect(checkAuthCode(SHARE).hint).toMatch(/That’s a match sharing code/);
    expect(checkAuthCode("AB12-CDE3").hint).toMatch(/13 letters and digits.*\(this has 8\)/);
    expect(checkAuthCode("").ok).toBe(false);
    expect(checkAuthCode("", true).ok).toBe(true);  // linked: empty keeps the stored code

    expect(checkShareCode(LINK)).toEqual({ value: SHARE, ok: true, hint: null });
    expect(checkShareCode("ab12-cde34-fg56").hint).toMatch(/That’s your Game Authentication Code/);
    expect(checkShareCode(SHARE.replace("CSGO", "csgo")).hint).toMatch(/case-sensitive/);
    expect(checkShareCode("CSGO-abc").hint).toMatch(/5 groups of 5/);
    expect(checkShareCode("").ok).toBe(false);
    expect(checkShareCode("  ", true)).toEqual({ value: "", ok: true, hint: null });  // no recent match yet: link without one
    expect(checkShareCode("CSGO-abc", true).ok).toBe(false);  // optional, but what is typed must still be valid
  });

  it("maps server errors to the box to fix", () => {
    expect(errorField("invalid_auth_code")).toBe("auth");
    expect(errorField("auth_code_is_share_code")).toBe("auth");
    expect(errorField("invalid_share_code")).toBe("share");
    expect(errorField("share_code_is_auth_code")).toBe("share");
    expect(errorField("share_code_required")).toBe("share");
    expect(errorField("valve_rate_limited")).toBeNull();
  });
});
