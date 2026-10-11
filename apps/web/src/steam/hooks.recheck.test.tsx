import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useMe } from "./hooks";
import { advance, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";

describe("useMe background re-check", () => {
  it("focus re-check keeps loading false and the same me object (no list remount / skeleton flash)", async () => {
    vi.useFakeTimers();
    installFakeApi({ "GET /me": { status: 200, body: meFixture } });
    const { result } = renderHook(() => useMe(true));
    await advance();
    const first = result.current.me;
    const seen: boolean[] = [];
    await act(async () => { window.dispatchEvent(new Event("focus")); seen.push(result.current.loading); });
    await advance();
    seen.push(result.current.loading);
    expect(seen).toEqual([false, false]);
    expect(result.current.me).toBe(first);
    vi.useRealTimers();
  });
});
