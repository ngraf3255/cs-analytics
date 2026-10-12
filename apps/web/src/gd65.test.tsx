import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MatchesTeaser } from "./steam/MatchesTeaser";
import { ShareButton } from "./steam/ShareButton";

describe("GD #65 closers", () => {
  it("the sample is tagged 'Example' on the card, with no SAMPLE REPORT kicker", () => {
    render(<MatchesTeaser />);
    const fig = screen.getByRole("figure", { name: "Example match report" });
    expect(fig.querySelector(".teaser-report .example-tag")).toHaveTextContent(/^Example$/);
    expect(fig).not.toHaveTextContent("SAMPLE REPORT");
  });
  it("Share uses the SVG arrow, not a text glyph", () => {
    render(<ShareButton label="Share" card={() => ({}) as never} />);
    const button = screen.getByRole("button", { name: /Share/ });
    expect(button.textContent).not.toContain("↗");
    expect(button.querySelector("svg.icon")).not.toBeNull();
  });
});

import { IconText } from "./Icon";
describe("GD #66", () => {
  it("IconText applies one shared gap between text and icon", () => {
    const { container } = render(<a className="steam-button" href="#x"><IconText>Sign in through Steam</IconText></a>);
    const wrap = container.querySelector(".icon-text")!;
    expect(wrap).toHaveTextContent("Sign in through Steam");
    expect(wrap.lastElementChild?.matches("svg.icon")).toBe(true);
  });
});
