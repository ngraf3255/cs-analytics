import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MapName } from "./MapName";

describe("MapName", () => {
  it("wraps digits in the mono slashed-zero span", () => {
    const { container } = render(<p><MapName map="rush_001" /></p>);
    expect(container.textContent).toBe("Rush 001");
    const digits = container.querySelectorAll(".map-digits");
    expect(digits).toHaveLength(1);
    expect(digits[0].textContent).toBe("001");
  });
  it("no digit span when there are no digits", () => {
    const { container } = render(<p><MapName map="de_mirage" /></p>);
    expect(container.textContent).toBe("Mirage");
    expect(container.querySelector(".map-digits")).toBeNull();
  });
});
