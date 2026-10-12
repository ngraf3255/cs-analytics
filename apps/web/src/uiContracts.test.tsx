import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Icon, Tip } from "./Icon";

describe("lintapple contracts", () => {
  it("icons are decorative SVGs, never text glyphs", () => {
    const { container } = render(<button type="button" aria-label="Dismiss tip"><Icon name="close" /></button>);
    const svg = container.querySelector("svg.icon")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "Dismiss tip" }).textContent).toBe("");
  });
  it("an icon-only control links a text tooltip", () => {
    render(<button type="button" aria-label="Open menu" aria-describedby="t1"><Icon name="info" /><Tip id="t1">Menu</Tip></button>);
    const button = screen.getByRole("button", { name: "Open menu" });
    const tip = document.getElementById(button.getAttribute("aria-describedby")!)!;
    expect(tip).toHaveAttribute("role", "tooltip");
    expect(tip).toHaveTextContent("Menu");
    expect(button).toHaveAccessibleDescription("Menu");
  });
});
