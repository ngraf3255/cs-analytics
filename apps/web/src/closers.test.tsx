import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useHashScroll } from "./steam/hooks";
import { goHomeTop } from "./steam/routes";

describe("sign-out lands on / at the top", () => {
  afterEach(() => { window.history.replaceState(null, "", "/"); });
  it("on the home page: drops #matches and jumps to the top", () => {
    window.history.replaceState(null, "", "/#matches");
    const scrollTo = vi.fn(); vi.stubGlobal("scrollTo", scrollTo);
    goHomeTop();
    expect(window.location.pathname + window.location.hash).toBe("/");
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "instant" });
  });
});

describe("#matches load jump is instant", () => {
  let resize: () => void = () => undefined;
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { constructor(cb: () => void) { resize = cb; } observe() {} disconnect() {} });
    window.history.replaceState(null, "", "/#matches");
    document.body.innerHTML = '<section id="matches"></section>';
  });
  afterEach(() => { window.history.replaceState(null, "", "/"); document.body.innerHTML = ""; });
  it("jumps with behavior 'instant' on mount and on every re-pin; a hash link still glides", () => {
    const spy = vi.fn(); document.getElementById("matches")!.scrollIntoView = spy;
    renderHook(() => useHashScroll(true));
    expect(spy).toHaveBeenLastCalledWith({ block: "start", behavior: "instant" });
    resize();
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy).toHaveBeenLastCalledWith({ block: "start", behavior: "instant" });
    window.dispatchEvent(new HashChangeEvent("hashchange"));
    expect(spy).toHaveBeenLastCalledWith({ block: "start" });
  });
});
