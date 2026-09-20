import { describe, expect, it } from "vitest";

import { API_BASE, resolveApiBase } from "./api-base";

describe("resolveApiBase", () => {
  it("uses the shared application API base by default", () => {
    expect(resolveApiBase()).toBe(API_BASE);
  });

  it("preserves an explicit endpoint for isolated runtimes and tests", () => {
    expect(resolveApiBase("https://api.example.test")).toBe(
      "https://api.example.test"
    );
  });
});
