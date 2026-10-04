import { afterEach, describe, expect, it } from "vitest";
import { apiBase } from "./api";

afterEach(() => { delete (globalThis as any).window; delete process.env.NEXT_PUBLIC_API_URL; });

describe("apiBase", () => {
  it("falls back to localhost", () => { expect(apiBase()).toBe("http://127.0.0.1:8088"); });
  it("uses the build-time variable when there is no run-time config", () => {
    process.env.NEXT_PUBLIC_API_URL = "https://build.example.com";
    expect(apiBase()).toBe("https://build.example.com");
  });
  it("prefers /config.js, so a deployed site can be re-pointed without a rebuild", () => {
    process.env.NEXT_PUBLIC_API_URL = "https://build.example.com";
    (globalThis as any).window = { ALEXANDRIA_CONFIG: { apiUrl: "https://tunnel.example.com/" } };
    expect(apiBase()).toBe("https://tunnel.example.com");         // trailing slash removed
  });
  it("ignores an empty config", () => {
    (globalThis as any).window = { ALEXANDRIA_CONFIG: {} };
    expect(apiBase()).toBe("http://127.0.0.1:8088");
  });
});
