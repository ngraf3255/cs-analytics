import { render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { steamApi } from "./api";
import { useMe } from "./hooks";
import { SteamSection } from "./SteamSection";
import { advance, installFakeApi } from "../test/fakeApi";
import meFixture from "../test/fixtures/me.json";

const HINT = "csa_has_session";
const STATUS = { "GET /steam/status": { status: 200, body: { enabled: true, steam: true, upload: true } } };

describe("session hint on sign-out", () => {
  beforeEach(() => { vi.useFakeTimers(); window.localStorage.clear(); window.localStorage.setItem(HINT, "1"); });

  it("sign-out and delete clear the hint synchronously, before the request answers or any navigation", () => {
    installFakeApi({ "POST /auth/logout": () => new Promise(() => undefined), "DELETE /me": () => new Promise(() => undefined) });
    void steamApi.logout();
    expect(window.localStorage.getItem(HINT)).toBeNull();
    window.localStorage.setItem(HINT, "1");
    void steamApi.deleteMe();
    expect(window.localStorage.getItem(HINT)).toBeNull();
  });

  it("a /me success that started before sign-out does not set the hint (or me) again", async () => {
    let answer: (v: { status: number; body: unknown }) => void = () => undefined;
    installFakeApi({ "GET /me": () => new Promise((r) => { answer = r; }), "POST /auth/logout": { status: 204 } });
    const { result } = renderHook(() => useMe(true));
    await advance();
    void steamApi.logout();
    answer({ status: 200, body: meFixture });
    await advance();
    expect(window.localStorage.getItem(HINT)).toBeNull();
    expect(result.current.me).toBeNull();
  });

  it("Connect Steam is not mounted while the hint is set and auth is pending; appears once auth says signed out", async () => {
    let answer: (v: { status: number; body: unknown }) => void = () => undefined;
    installFakeApi({ ...STATUS, "GET /me": () => new Promise((r) => { answer = r; }) });
    render(<SteamSection />);
    await advance();
    expect(screen.queryByText("Connect Steam")).toBeNull();
    answer({ status: 401, body: { detail: "not_signed_in" } });
    await advance();
    expect(screen.getByText("Connect Steam")).toBeInTheDocument();
    expect(window.localStorage.getItem(HINT)).toBeNull();
  });
});
