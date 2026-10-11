import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { resetMeRequest } from "../steam/api";

afterEach(() => {
  cleanup();
  resetMeRequest();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
