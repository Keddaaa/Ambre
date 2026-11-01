import { describe, expect, it } from "vitest";

import { ApiError, isNotFoundWith } from "./api";

describe("isNotFoundWith", () => {
  it("accepts a 404 whose detail starts with the expected prefix", () => {
    expect(isNotFoundWith(new ApiError(404, "unknown market abc"), "unknown market")).toBe(true);
  });
  it("rejects a 404 from another source (wrong base URL, proxy, missing route)", () => {
    expect(isNotFoundWith(new ApiError(404, "Not Found"), "unknown market")).toBe(false);
  });
  it("rejects other statuses and non-API errors", () => {
    expect(isNotFoundWith(new ApiError(500, "unknown market abc"), "unknown market")).toBe(false);
    expect(isNotFoundWith(new Error("boom"), "unknown market")).toBe(false);
  });
});
